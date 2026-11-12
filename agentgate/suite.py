"""Run a suite (every case x every config x reps), summarise it, gate on it."""

import importlib
import json
import math
import statistics

from . import pricing, scorers
from .trace import Trace

DEFAULT_JUDGE = "claude-sonnet-5-5"
ROUTER_RUBRIC = (
    "Pass only if the answer fully addresses the task and every fact in it is "
    "supported by the transcript. Fail if the agent skipped a lookup it needed, or guessed."
)
SCORED = ("pass", "fail", "refusal")  # error and truncated trials measured the plumbing, not the model


def resolve(path):
    """'package.module:name' -> the object."""
    module, _, name = path.partition(":")
    return getattr(importlib.import_module(module), name)


def tiers(cfg):
    return cfg["router"]["tiers"] if "router" in cfg else [cfg]


def load(path):
    with open(path) as f:
        suite = json.load(f)
    pricing.PRICES.update({model: tuple(p) for model, p in suite.get("prices", {}).items()})
    models = [t["model"] for cfg in suite["configs"] for t in tiers(cfg)] + [suite.get("judge_model", DEFAULT_JUDGE)]
    unpriced = sorted(set(models) - set(pricing.PRICES))
    if unpriced:
        raise SystemExit(f"no price for {unpriced}: add them under \"prices\" in {path}")
    return suite


def make_client(suite):
    if "client" in suite:
        return resolve(suite["client"])()
    import anthropic  # only needed for real runs
    # A short connect timeout: a dead network fails in seconds instead of hanging for minutes per retry.
    return anthropic.Anthropic(timeout=anthropic.Timeout(300.0, connect=10.0))


def run_agent(agent, client, cfg, task, spans, judge_model):
    """Run the agent on the first tier; move up a tier while the gate rejects the answer.

    Appends every span to `spans` (so they survive a crash) and returns
    (output, spans of the attempt that was kept).
    """
    router = cfg.get("router")
    tier_list = tiers(cfg)
    for i, tier in enumerate(tier_list):
        trace = Trace(client, tier["model"], tier.get("effort"))
        try:
            output = agent(task, trace)
        finally:
            spans += [{**s, "tier": i} for s in trace.spans]
        if i == len(tier_list) - 1:
            break
        accept = router.get("accept", "judge")
        if accept == "judge":
            ok, _, span = scorers.judge(client, judge_model, ROUTER_RUBRIC, task, output, trace.spans, kind="gate")
            spans.append({**span, "tier": i})
        else:
            ok = resolve(accept)(task, output, trace.spans)
        if ok:
            break
    return output, trace.spans


def run_trial(suite, cfg, case, rep, client, agent):
    judge_model = suite.get("judge_model", DEFAULT_JUDGE)
    spans, output, detail = [], "", ""
    try:
        output, final = run_agent(agent, client, cfg, case["input"], spans, judge_model)
        stop = next((s["stop_reason"] for s in reversed(final) if s["kind"] == "llm"), None)
        if stop == "max_tokens":
            status = "truncated"
        elif stop == "refusal":
            status = "refusal"
        else:
            failures, judge_spans = scorers.run_checks(
                case["checks"], case["input"], output, final,
                lambda *args: scorers.judge(client, judge_model, *args),
            )
            spans += judge_spans
            status, detail = ("fail", json.dumps(failures)) if failures else ("pass", "")
    except Exception as e:  # API, agent or grader failure: record it, never score it as a wrong answer
        status, detail = "error", f"{type(e).__name__}: {e}"
    agent_spans = [s for s in spans if s["kind"] != "judge"]
    return {
        "config": cfg["name"], "case_id": case["id"], "rep": rep, "input": case["input"],
        "status": status, "output": output, "detail": detail,
        "cost": sum(s["cost"] for s in agent_spans),  # what production would pay, router gate included
        "eval_cost": sum(s["cost"] for s in spans if s["kind"] == "judge"),
        "latency_ms": sum(s["ms"] for s in agent_spans),
        "tiers": 1 + max((s.get("tier", 0) for s in spans), default=0),
        "spans": spans,
    }


def run(suite, client, reps=1, only=None, configs=None, on_trial=lambda row: None):
    agent = resolve(suite["agent"])
    rows = []
    for cfg in suite["configs"]:
        if configs and cfg["name"] not in configs:
            continue
        for case in suite["cases"]:
            if only and case["id"] not in only:
                continue
            # shortcut: trials run one at a time; add a thread pool when a suite takes too long.
            for rep in range(reps):
                rows.append(run_trial(suite, cfg, case, rep, client, agent))
                on_trial(rows[-1])
    return rows


def wilson(passed, n, z=1.96):
    """95% confidence interval for a pass rate."""
    if n == 0:
        return (0.0, 0.0)
    p = passed / n
    centre, margin = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / (1 + z * z / n), (centre + margin) / (1 + z * z / n))


def summarize(rows):
    by_config = {}
    for r in rows:
        by_config.setdefault(r["config"], []).append(r)
    out = []
    for name, rs in by_config.items():
        scored = [r for r in rs if r["status"] in SCORED]
        passed = sum(r["status"] == "pass" for r in scored)
        out.append({
            "config": name, "scored": len(scored), "passed": passed,
            "pass_rate": passed / len(scored) if scored else 0.0,
            "ci": wilson(passed, len(scored)),
            "errors": sum(r["status"] == "error" for r in rs),
            "truncated": sum(r["status"] == "truncated" for r in rs),
            "refusals": sum(r["status"] == "refusal" for r in rs),
            "cost": statistics.mean(r["cost"] for r in rs),
            "eval_cost": sum(r["eval_cost"] for r in rs),
            "latency_ms": statistics.median(r["latency_ms"] for r in rs),
            "escalated": sum(r["tiers"] > 1 for r in rs) / len(rs),
        })
    return out


def baseline(rows):
    """{config: {case_id: share of scored trials that passed}}."""
    counts = {}
    for r in rows:
        if r["status"] in SCORED:
            passed, n = counts.setdefault(r["config"], {}).get(r["case_id"], (0, 0))
            counts[r["config"]][r["case_id"]] = (passed + (r["status"] == "pass"), n + 1)
    return {cfg: {case: p / n for case, (p, n) in cases.items()} for cfg, cases in counts.items()}


def gate(rows, base, tolerance=0.05):
    """Problems that should fail CI: a pass rate below the baseline, or trials that could not be measured."""
    problems = []
    unmeasured = [f"{r['config']}/{r['case_id']} ({r['status']})" for r in rows if r["status"] not in SCORED]
    if unmeasured:
        problems.append("could not measure: " + ", ".join(unmeasured))
    new = baseline(rows)
    for cfg, old_cases in base.items():
        if cfg not in new:
            continue  # config not run this time (e.g. --configs)
        shared = [c for c in old_cases if c in new[cfg]]
        if not shared:
            continue
        old_rate = sum(old_cases[c] for c in shared) / len(shared)
        new_rate = sum(new[cfg][c] for c in shared) / len(shared)
        if new_rate < old_rate - tolerance:
            dropped = [c for c in shared if new[cfg][c] < old_cases[c]]
            problems.append(f"{cfg}: pass rate {old_rate:.0%} -> {new_rate:.0%}, regressed on {', '.join(dropped)}")
    return problems
