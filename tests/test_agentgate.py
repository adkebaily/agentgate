"""Offline tests: python -m unittest (no API key, no network)."""

import copy
import json
import unittest
from types import SimpleNamespace as NS

from agentgate import pricing, report, scorers, suite as suite_mod
from agentgate.trace import Trace
from examples.sim_llm import SimClient

SUITE = suite_mod.load("examples/suite.sim.json")
ROWS = suite_mod.run(SUITE, SimClient())
SUMMARY = {s["config"]: s for s in suite_mod.summarize(ROWS)}


class StubClient:
    """Returns one fixed text answer, whatever is asked."""

    def __init__(self, text, stop_reason="end_turn", served_as=None):
        self.messages, self.text, self.stop_reason, self.served_as = self, text, stop_reason, served_as

    def create(self, *, model, **_):
        return NS(model=self.served_as or model, stop_reason=self.stop_reason,
                  content=[NS(type="text", text=self.text)], usage=NS(input_tokens=1000, output_tokens=100))


def one_trial(client, checks, agent=lambda task, trace: trace.llm(messages=[]).content[0].text):
    suite = {"judge_model": "sim-large"}
    return suite_mod.run_trial(suite, {"name": "c", "model": "sim-large"}, {"id": "x", "input": "q", "checks": checks}, 0, client, agent)


class SimSuite(unittest.TestCase):
    def test_pass_rates(self):
        self.assertEqual((SUMMARY["small"]["passed"], SUMMARY["large"]["passed"], SUMMARY["router"]["passed"]), (8, 12, 12))

    def test_router_keeps_quality_for_less(self):
        self.assertLess(SUMMARY["router"]["cost"], SUMMARY["large"]["cost"])
        self.assertAlmostEqual(SUMMARY["router"]["escalated"], 4 / 12)  # the 4 even-numbered orders

    def test_router_cost_counts_the_rejected_attempt(self):
        row = next(r for r in ROWS if r["config"] == "router" and r["case_id"] == "late-61-days")
        self.assertEqual(row["tiers"], 2)
        self.assertEqual({s["model"] for s in row["spans"] if s["kind"] == "llm"}, {"sim-small", "sim-large"})


class Gate(unittest.TestCase):
    def test_same_run_passes(self):
        self.assertEqual(suite_mod.gate(ROWS, suite_mod.baseline(ROWS)), [])

    def test_regression_is_named(self):
        worse = copy.deepcopy(ROWS)
        next(r for r in worse if r["config"] == "large" and r["case_id"] == "final-sale")["status"] = "fail"
        problems = suite_mod.gate(worse, suite_mod.baseline(ROWS))
        self.assertEqual(len(problems), 1)
        self.assertIn("large", problems[0])
        self.assertIn("final-sale", problems[0])

    def test_unmeasured_trial_fails_the_gate(self):
        broken = copy.deepcopy(ROWS)
        broken[0]["status"] = "error"
        self.assertIn("could not measure", suite_mod.gate(broken, suite_mod.baseline(ROWS))[0])


class Statuses(unittest.TestCase):
    def test_agent_crash_is_an_error_not_a_fail(self):
        def crash(task, trace):
            raise ValueError("boom")
        row = one_trial(StubClient("x"), [], agent=crash)
        self.assertEqual(row["status"], "error")
        self.assertEqual(suite_mod.summarize([row])[0]["scored"], 0)

    def test_cut_off_answer_is_not_scored(self):
        self.assertEqual(one_trial(StubClient("x", stop_reason="max_tokens"), [])["status"], "truncated")

    def test_refusal_is_its_own_status(self):
        self.assertEqual(one_trial(StubClient("", stop_reason="refusal"), [])["status"], "refusal")

    def test_wrong_served_model_is_an_error(self):
        row = one_trial(StubClient("x", served_as="other-model"), [])
        self.assertEqual(row["status"], "error")
        self.assertIn("served by other-model", row["detail"])


class Scoring(unittest.TestCase):
    def test_code_checks(self):
        spans = [{"kind": "tool", "name": "get_order"}]
        checks = [{"type": "contains", "value": "42.5"}, {"type": "not_contains", "value": "999"},
                  {"type": "regex", "value": r"^\W*ELIGIBLE"}, {"type": "tool_called", "name": "get_order"},
                  {"type": "tool_not_called", "name": "get_refund_policy"}]
        self.assertEqual(scorers.run_checks(checks, "q", "ELIGIBLE - 42.50 euros", spans, None)[0], [])
        failures, _ = scorers.run_checks(checks, "q", "NOT ELIGIBLE", spans, None)
        self.assertEqual([f["type"] for f in failures], ["contains", "regex"])

    def test_judge_verdict_and_separate_cost(self):
        client = StubClient(json.dumps({"reason": "too vague", "pass": False}))
        row = one_trial(client, [{"type": "judge", "rubric": "be specific"}])
        self.assertEqual(row["status"], "fail")
        self.assertIn("too vague", row["detail"])
        self.assertGreater(row["eval_cost"], 0)
        self.assertAlmostEqual(row["cost"], row["eval_cost"])  # one agent call, one judge call, same stub usage

    def test_cost(self):
        usage = NS(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_input_tokens=1_000_000)
        self.assertAlmostEqual(pricing.cost("claude-fable-5-2", usage), 8 + 40 + 0.20)

    def test_tool_error_reaches_the_model(self):
        trace = Trace(SimClient(), "sim-large")
        self.assertEqual(trace.tool("nope", None), ("Error: unknown tool nope", True))


class Interrupt(unittest.TestCase):
    def test_ctrl_c_keeps_finished_trials_and_skips_the_baseline(self):
        import os, subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db, baseline = os.path.join(tmp, "t.db"), os.path.join(tmp, "b.json")
            # Interrupt after the 3rd trial by making the 4th agent call raise KeyboardInterrupt.
            code = (
                "import sys, examples.support_agent as a; real = a.run; n = [0]\n"
                "def run(task, trace):\n"
                "    n[0] += 1\n"
                "    if n[0] == 4: raise KeyboardInterrupt\n"
                "    return real(task, trace)\n"
                "a.run = run\n"
                f"sys.argv = ['agentgate', '--db', {db!r}, 'run', 'examples/suite.sim.json', '--report', {os.path.join(tmp, 'r.html')!r}, '--baseline', {baseline!r}, '--save-baseline']\n"
                "from agentgate.cli import main; main()\n"
            )
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("3 trials kept", proc.stderr)
            self.assertFalse(os.path.exists(baseline))
            from agentgate import store
            self.assertEqual(len(store.load_run(store.connect(db))[2]), 3)


class Report(unittest.TestCase):
    def test_report_escapes_model_output(self):
        rows = copy.deepcopy(ROWS[:1])
        rows[0]["output"] = "<script>alert(1)</script>"
        html = report.build("s", 1, rows)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
