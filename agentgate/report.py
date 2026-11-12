"""One self-contained HTML page for a run: scoreboard, case matrix, transcripts."""

from html import escape

from .suite import summarize
from .trace import render

CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --muted:#666; --line:#ddd; --pass:#1a7f37; --fail:#c62828; --warn:#9a6700; --card:#f6f6f6; }
@media (prefers-color-scheme: dark) { :root { --bg:#15171a; --fg:#e6e6e6; --muted:#9aa0a6; --line:#33373d; --pass:#4ac26b; --fail:#ff6b6b; --warn:#e3b341; --card:#1e2126; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; max-width:1000px; margin:0 auto; padding:24px 16px; }
h1 { font-size:22px; margin:0 0 4px } h2 { font-size:17px; margin:32px 0 8px } .muted { color:var(--muted) }
.scroll { overflow-x:auto } table { border-collapse:collapse; width:100% } th,td { padding:6px 10px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums }
.pass { color:var(--pass) } .fail,.refusal { color:var(--fail) } .error,.truncated { color:var(--warn) }
details { background:var(--card); border-radius:6px; padding:8px 12px; margin:6px 0 } summary { cursor:pointer }
pre { white-space:pre-wrap; word-break:break-word; font-size:13px; margin:8px 0 0 }
"""
MARK = {"pass": "✓", "fail": "✗", "refusal": "refused", "error": "error", "truncated": "cut off"}


def build(suite_name, run_id, rows):
    h = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>AgentGate run {run_id}</title><style>{CSS}</style>"
         f"<h1>{escape(suite_name)}</h1><div class=muted>run {run_id} · {len(rows)} trials</div>"]

    h.append("<h2>Scoreboard</h2><div class=scroll><table><tr><th>Config<th class=num>Pass rate<th class=num>95% interval"
             "<th class=num>Cost / task<th class=num>Median latency<th class=num>Escalated<th class=num>Not measured</tr>")
    for s in summarize(rows):
        unmeasured = s["errors"] + s["truncated"]
        h.append(
            f"<tr><td>{escape(s['config'])}<td class=num>{s['pass_rate']:.0%} ({s['passed']}/{s['scored']})"
            f"<td class=num>{s['ci'][0]:.0%} – {s['ci'][1]:.0%}<td class=num>${s['cost']:.5f}"
            f"<td class=num>{s['latency_ms'] / 1000:.2f} s<td class=num>{s['escalated']:.0%}"
            f"<td class='num {'error' if unmeasured else ''}'>{unmeasured}</tr>")
    h.append("</table></div><p class=muted>Cost / task is what the agent itself spends, router gate included; "
             "grading cost is separate. Errors and cut-off answers are left out of the pass rate.</p>")

    configs = list(dict.fromkeys(r["config"] for r in rows))
    cases = list(dict.fromkeys(r["case_id"] for r in rows))
    cell = {}
    for r in rows:
        cell.setdefault((r["case_id"], r["config"]), []).append(r["status"])
    h.append("<h2>Cases</h2><div class=scroll><table><tr><th>Case" + "".join(f"<th>{escape(c)}" for c in configs) + "</tr>")
    for case in cases:
        h.append(f"<tr><td>{escape(case)}")
        for cfg in configs:
            h.append("<td>" + " ".join(f"<span class={s}>{MARK[s]}</span>" for s in cell.get((case, cfg), [])))
        h.append("</tr>")
    h.append("</table></div>")

    h.append("<h2>Transcripts</h2>")
    for r in sorted(rows, key=lambda r: r["status"] == "pass"):  # problems first
        h.append(
            f"<details><summary><span class={r['status']}>{MARK[r['status']]}</span> "
            f"{escape(r['config'])} · {escape(r['case_id'])} · rep {r['rep']} · ${r['cost']:.5f}</summary>"
            f"<pre>task: {escape(r['input'])}\n\n{escape(render(r['spans']))}\n\nanswer: {escape(r['output'])}"
            + (f"\n\n{escape(r['status'])}: {escape(r['detail'])}" if r["detail"] else "") + "</pre></details>")
    return "".join(h)
