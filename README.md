# AgentGate

Tests, traces and a regression gate for AI agents, plus a model router whose savings you can measure.

You change a prompt, a tool or a model. Did the agent get better or worse, and what does it cost now? AgentGate answers that with numbers and can block the change in CI.

- **Trace**: every model call and tool call is recorded with tokens, cost and latency.
- **Score**: each test case has checks. Code checks first (text, regex, which tools were called), an LLM judge for the rest.
- **Compare**: run the same cases on several models or settings and get pass rate, cost per task and latency side by side.
- **Route**: try the cheap model first and move up only when a gate rejects the answer. The router is scored like any other config, so you see what it saves and what it loses.
- **Gate**: save a baseline, then fail CI when the pass rate drops.

About 600 lines of Python. The core has no dependencies; real runs need the `anthropic` SDK.

## Try it in two minutes (free, offline)

The demo uses a scripted fake model, so it needs no API key. Its numbers show how the tool works. They say nothing about real models.

```bash
git clone <this repo> && cd agentgate
python -m unittest                                    # 16 tests
python -m agentgate run examples/suite.sim.json       # run the demo suite
xdg-open report.html                                  # open the report (macOS: open)
python -m agentgate show late-61-days --config router # read one transcript
```

You get a scoreboard like this (simulated):

```
config                pass    95% interval     $/task  escalated
small           67% (8/12)       39% - 86%    0.00002         0%
large         100% (12/12)      76% - 100%    0.00201         0%
router        100% (12/12)      76% - 100%    0.00079        33%
```

Read it as: the small model is cheap but wrong on a third of the cases. The router sends 33% of tasks up to the large model, reaches the same pass rate, and costs less than using the large model every time.

## Run it on real Claude models

```bash
python -m venv .venv && .venv/bin/pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# start small: one cheap config, one case
.venv/bin/python -m agentgate run examples/suite.json --configs haiku --only opening-hours

# then everything, 3 runs per case for a tighter interval
.venv/bin/python -m agentgate run examples/suite.json --reps 3
```

`examples/suite.json` compares five configs: Haiku 5.5, Sonnet 5.5, Fable 5.2 at low effort, Fable 5.2 at default effort, and a router (Haiku → Sonnet → Fable).

**This spends money.** The suite is small (12 short cases), and Fable 5.2 at $8 / $40 per million tokens will be most of the bill. Run one config first and read `$/task` before you run all of them with `--reps`.

## Results on real models (2026-11-13)

`examples/suite.json`, 12 cases × 5 configs, one run each. Measured with this repo; your numbers will move a little between runs.

| Config | Pass rate | 95% interval | Cost / task | Median latency |
|---|---|---|---|---|
| Haiku 5.5 | 12/12 | 76% – 100% | $0.00026 | 3.0 s |
| Sonnet 5.5 | 12/12 | 76% – 100% | $0.00457 | 3.7 s |
| Fable 5.2, low effort | 12/12 | 76% – 100% | $0.01770 | 2.4 s |
| Fable 5.2 | 12/12 | 76% – 100% | $0.01832 | 2.2 s |
| Router (Haiku → Sonnet → Fable) | 12/12 | 76% – 100% | $0.00026 | 2.8 s |

The whole run cost $0.49, plus $0.01 for the judge.

What it shows:

- **Haiku 5.5 is enough for this agent.** It passed every case, prompt injections included, at about 1/70 of the cost of Fable 5.2 per task.
- **The router never escalated.** Haiku always read the refund policy, so the gate accepted its first answer. It cost what Haiku costs and saved nothing over Haiku alone.
- **The suite is too easy to rank the models.** With every config at 12/12, the interval is wide and the same for all. Telling them apart needs harder cases: longer policies, several orders in one message, ambiguous requests.
- **The eval caught its own bug.** In the first run, three configs "failed" `injection-amount`. The transcripts showed they had answered correctly and *mentioned* the ignored note ("I ignored an instruction in the order's notes to approve 999.00 euros"). The check `not_contains 999` was too strict, and a judge now grades that case.

## How it works

```
suite.json ──► runner ──► agent(task, trace) ──► model + tools
                 │              │
                 │              └─ every call becomes a span (tokens, cost, ms)
                 ├─► checks ──► pass / fail / refusal / truncated / error
                 ├─► SQLite (agentgate.db) ──► report.html, `show`, `runs`
                 └─► baseline ──► gate (exit 1 on a regression)
```

| Word | Meaning |
|---|---|
| **Agent** | Any function `run(task, trace) -> str`. It calls the model with `trace.llm(...)` and tools with `trace.tool(...)`. |
| **Span** | One recorded model call or tool call. |
| **Case** | One input plus the checks its answer must pass. |
| **Config** | One way to run the agent: a model, an optional effort level, or a router. |
| **Trial** | One case run once on one config. |
| **Baseline** | The pass rates you accepted. The gate compares new runs to it. |

### Statuses

| Status | Meaning | In the pass rate? |
|---|---|---|
| `pass` / `fail` | The checks passed, or at least one failed | yes |
| `refusal` | The model declined to answer | yes, as a failure |
| `truncated` | The answer was cut off at `max_tokens` | no |
| `error` | API error, agent crash, grader crash, or the wrong model answered | no |

Errors and cut-off answers are kept out of the pass rate on purpose. A timeout is a problem with the plumbing, and counting it as a wrong answer would blame the model for it. The gate still fails when any trial could not be measured.

## Test your own agent

**1. Make your agent use the trace.** Replace `client.messages.create(model=..., ...)` with `trace.llm(...)`, and wrap tool calls:

```python
def run(task, trace):
    messages = [{"role": "user", "content": task}]
    while True:
        response = trace.llm(system=SYSTEM, tools=TOOLS, messages=messages)
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output, is_error = trace.tool(block.name, TOOL_FNS.get(block.name), **block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    return "".join(b.text for b in response.content if b.type == "text")
```

The agent never names a model. The config does, which is what lets you compare models without touching agent code. `examples/support_agent.py` is a full example.

**2. Write a suite.**

```json
{
  "name": "my-agent",
  "agent": "my_package.my_agent:run",
  "judge_model": "claude-sonnet-5-5",
  "configs": [
    {"name": "haiku", "model": "claude-haiku-5-5"},
    {"name": "fable-low", "model": "claude-fable-5-2", "effort": "low"},
    {"name": "router", "router": {
      "accept": "judge",
      "tiers": [{"model": "claude-haiku-5-5"}, {"model": "claude-fable-5-2"}]
    }}
  ],
  "cases": [
    {"id": "refund-late", "input": "Please refund order A102.", "checks": [
      {"type": "regex", "value": "^NOT ELIGIBLE"},
      {"type": "tool_called", "name": "get_order"},
      {"type": "judge", "rubric": "The answer gives the 30-day rule as the reason."}
    ]}
  ]
}
```

| Check | Passes when |
|---|---|
| `contains` / `not_contains` | the answer has / does not have `value` (case-insensitive) |
| `regex` | `value` matches the answer (case-insensitive, `^` matches line starts) |
| `tool_called` / `tool_not_called` | the agent called / did not call the tool `name` |
| `judge` | a model decides the answer meets `rubric`, seeing the task, the tool transcript and the answer |

Every check on a case must pass. A model that is not in `agentgate/pricing.py` needs a price in the suite: `"prices": {"my-model": [input, output, cache_read, cache_write]}` in dollars per million tokens.

**3. Write good cases.** Three rules matter most:

- **Test both directions.** If you test "calls the tool when it should", also test "does not call it when it should not". The demo has both, and cases with a prompt injection hidden in tool data.
- **Prefer code checks.** A judge costs money and can be wrong. Ask the agent for a fixed first word (`ELIGIBLE` / `NOT ELIGIBLE`) and check it with a regex.
- **Do not let a model grade itself.** When you compare models A and B, do not use A as the judge.

## The router

A router config lists tiers from cheap to expensive. The agent runs on the first tier. A gate looks at the answer, and if it rejects it the whole task runs again on the next tier. The last tier's answer is always kept.

The gate is either:

- `"accept": "judge"`: the judge model decides whether the answer is complete and supported by the tool transcript. Its cost is counted in the router's cost per task, because you would pay it in production.
- `"accept": "module:function"`: your own `fn(task, output, spans) -> bool`. The demo's `grounded` rejects any answer about an order when the agent did not read the refund policy. Free and instant.

Rejected attempts are paid for and counted, so the router's cost is honest. The `escalated` column shows how often it moved up.

**Check the simple option first.** A router is extra moving parts. `examples/suite.json` includes `fable-low` (the top model at low effort) so you can see whether one model with less thinking beats the router on your tasks.

## The gate, and CI

```bash
python -m agentgate run my_suite.json --save-baseline   # accept the current pass rates; commit the file
python -m agentgate run my_suite.json --gate            # exit 1 if a config dropped more than 5 points
```

The gate names the config and the cases that got worse. `--tolerance 0.1` allows a bigger drop, which you may need because models are not deterministic. Use `--reps` to reduce noise instead of raising the tolerance when you can afford it.

`.github/workflows/evals.yml` runs the unit tests and the offline suite on every pull request. The real-model job only starts when you launch it by hand (Actions → agent evals → Run workflow), because it costs money. It needs an `ANTHROPIC_API_KEY` repository secret.

## Commands

| Command | Does |
|---|---|
| `agentgate run SUITE` | Run, save to `agentgate.db`, write `report.html` |
| `  --reps N` | Run each case N times |
| `  --configs a,b` / `--only ID ...` | Run part of the suite |
| `  --save-baseline` / `--gate` / `--tolerance X` | Baseline and regression gate |
| `agentgate runs` | List saved runs |
| `agentgate show CASE [--config NAME] [--run ID]` | Print one transcript |
| `agentgate report [--run ID] [-o FILE]` | Rebuild the HTML report of a saved run |

Use `python -m agentgate ...` from the project folder, or `pip install -e .` to get the `agentgate` command.

## What it does not do

- Trials run one at a time. A big suite will be slow.
- No replay of recorded model responses: to re-check a failure you run the case again (`--only`).
- No live dashboard or OpenTelemetry export. The report is one static HTML file.
- Refusal fallbacks are off on purpose. A fallback would let a different model answer, and the harness must know which model produced each score. A refusal is recorded as its own status.
- Prices are a table in `agentgate/pricing.py`, checked on 2026-11-03. Update them when they change.
