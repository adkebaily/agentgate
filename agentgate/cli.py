"""Command line: agentgate run | runs | show | report."""

import argparse
import json
import os
import sys

from . import report, store, suite as suite_mod
from .trace import render

MARK = {"pass": "✓", "fail": "✗", "refusal": "refused", "error": "ERROR", "truncated": "CUT OFF"}


def print_summary(rows):
    print(f"\n{'config':<14}{'pass':>12}{'95% interval':>16}{'$/task':>11}{'median s':>10}{'escalated':>11}{'unmeasured':>12}")
    for s in suite_mod.summarize(rows):
        rate = f"{s['pass_rate']:.0%} ({s['passed']}/{s['scored']})"
        interval = f"{s['ci'][0]:.0%} - {s['ci'][1]:.0%}"
        print(f"{s['config']:<14}{rate:>12}{interval:>16}{s['cost']:>11.5f}{s['latency_ms'] / 1000:>10.2f}"
              f"{s['escalated']:>11.0%}{s['errors'] + s['truncated']:>12}")


def cmd_run(args):
    suite = suite_mod.load(args.suite)
    rows = suite_mod.run(
        suite, suite_mod.make_client(suite), reps=args.reps, only=args.only,
        configs=args.configs.split(",") if args.configs else None,
        on_trial=lambda r: print(f"{MARK[r['status']]:>8}  {r['config']:<14}{r['case_id']:<28}${r['cost']:.5f}  {r['detail'][:80]}"),
    )
    if not rows:
        sys.exit("nothing ran: check --only and --configs")
    run_id = store.save_run(store.connect(args.db), suite["name"], args.reps, rows)
    print_summary(rows)
    with open(args.report, "w") as f:
        f.write(report.build(suite["name"], run_id, rows))
    print(f"\nrun {run_id} saved to {args.db}; report: {args.report}")

    baseline_path = args.baseline or args.suite.removesuffix(".json") + ".baseline.json"
    if args.save_baseline:
        with open(baseline_path, "w") as f:
            json.dump(suite_mod.baseline(rows), f, indent=2, sort_keys=True)
        print(f"baseline saved to {baseline_path}")
    if args.gate:
        if not os.path.exists(baseline_path):
            sys.exit(f"no baseline at {baseline_path}: run once with --save-baseline and commit it")
        with open(baseline_path) as f:
            problems = suite_mod.gate(rows, json.load(f), args.tolerance)
        if problems:
            sys.exit("GATE FAILED\n  " + "\n  ".join(problems))
        print("gate passed")


def cmd_runs(args):
    for run_id, name, started, reps, trials, passed in store.runs(store.connect(args.db)):
        print(f"run {run_id}  {started}  {name}  reps={reps}  {passed or 0}/{trials} trials passed")


def cmd_show(args):
    _, _, rows = store.load_run(store.connect(args.db), args.run)
    shown = [r for r in rows if r["case_id"] == args.case and (not args.config or r["config"] == args.config)]
    if not shown:
        sys.exit(f"no trial for case {args.case!r} in that run")
    for r in shown:
        print(f"=== {r['config']} · {r['case_id']} · rep {r['rep']} · {r['status']} · ${r['cost']:.5f}")
        print(f"task: {r['input']}\n{render(r['spans'])}\nanswer: {r['output']}")
        if r["detail"]:
            print(f"{r['status']}: {r['detail']}")
        print()


def cmd_report(args):
    run_id, name, rows = store.load_run(store.connect(args.db), args.run)
    with open(args.output, "w") as f:
        f.write(report.build(name, run_id, rows))
    print(f"report for run {run_id}: {args.output}")


def main():
    sys.path.insert(0, os.getcwd())  # so "module:function" paths in a suite resolve from the project folder
    p = argparse.ArgumentParser(prog="agentgate", description=__doc__)
    p.add_argument("--db", default="agentgate.db", help="SQLite file for runs (default: agentgate.db)")
    sub = p.add_subparsers(required=True)

    r = sub.add_parser("run", help="run a suite, save it, write the HTML report")
    r.add_argument("suite")
    r.add_argument("--reps", type=int, default=1, help="times to run each case (more reps = tighter interval, more cost)")
    r.add_argument("--only", nargs="+", metavar="CASE_ID", help="run only these cases")
    r.add_argument("--configs", help="comma-separated config names to run")
    r.add_argument("--report", default="report.html")
    r.add_argument("--baseline", help="baseline file (default: <suite>.baseline.json)")
    r.add_argument("--save-baseline", action="store_true", help="save this run's pass rates as the baseline")
    r.add_argument("--gate", action="store_true", help="exit 1 if the pass rate fell below the baseline")
    r.add_argument("--tolerance", type=float, default=0.05, help="allowed drop in pass rate (default 0.05)")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("runs", help="list saved runs").set_defaults(fn=cmd_runs)

    s = sub.add_parser("show", help="print the transcript of one case")
    s.add_argument("case")
    s.add_argument("--run", type=int, help="run id (default: latest)")
    s.add_argument("--config")
    s.set_defaults(fn=cmd_show)

    h = sub.add_parser("report", help="rebuild the HTML report of a saved run")
    h.add_argument("--run", type=int, help="run id (default: latest)")
    h.add_argument("-o", "--output", default="report.html")
    h.set_defaults(fn=cmd_report)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
