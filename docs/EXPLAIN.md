# How to explain AgentGate

Notes for presenting this project in an interview, on a CV or in a demo.

## In 30 seconds

> When you change an agent's prompt or model, you usually can't tell whether it got better. AgentGate runs the agent on a fixed set of test cases, records every model and tool call with its cost, and scores the answers. It compares models side by side, and it can fail a pull request when quality drops. It also has a model router, and because the router is scored like any other setup, I can show how much money it saves and how much quality it keeps.

## In two minutes (live demo)

1. `python -m agentgate run examples/suite.sim.json` and open `report.html`.
2. **Scoreboard.** "Each row is one way to run the agent. Pass rate, a confidence interval, cost per task, latency."
3. **Cases table.** "The small model fails these four. They are the cases where it skipped reading the refund policy."
4. **A transcript** (`show late-61-days --config router`). "Here the small model answered without the policy. The gate rejected that, the task ran again on the large model, and it got the right answer. Both attempts are in the cost."
5. **The gate.** Break something (delete the `get_refund_policy` line from the system prompt in `examples/support_agent.py`), run with `--gate` on real models, and show the non-zero exit and the named cases.

Say out loud that the offline demo uses a scripted fake model. Show real numbers only from real runs.

## The design decisions, and why

These are the questions an interviewer is likely to ask.

**Why not just use an existing tool (LangSmith, MLflow, Braintrust)?**
For a real team I would. I built this to understand what those tools do underneath: what a span is, how a judge is wired, why errors must be separated from failures. It is small enough to read in one sitting.

**Why are errors kept out of the pass rate?**
A timeout or a rate limit says nothing about the model. If it counts as a wrong answer, a flaky network makes a model look worse. So a trial is `pass`, `fail`, `refusal`, `truncated` or `error`, and only the first three are scored. The gate still fails if anything could not be measured, so problems are never hidden.

**Why code checks before an LLM judge?**
Code checks are free, instant and deterministic. A judge costs money, adds latency and is itself a model that can be wrong. I ask the agent to start with a fixed word (`ELIGIBLE` / `NOT ELIGIBLE`) so most cases need no judge. The judge is there for what code can't check, and it returns JSON through structured outputs, with the reason before the verdict so it reasons first.

**How does the router decide to escalate without knowing the right answer?**
It can't know the answer, so it checks the process. Either a judge reads the tool transcript and asks "is this answer supported by what the tools returned?", or a code rule does (the demo's rule: an answer about an order is rejected if the agent never read the policy). This catches the careless failures and misses the confident wrong ones. That limit is visible in the numbers, which is the point of measuring it.

**Why does the router rerun the whole task instead of switching model mid-conversation?**
Simpler and cleaner to measure: one model per attempt, so every answer has one owner. Thinking blocks from one model also can't always be read by another, so a mid-conversation switch loses reasoning.

**Is the router cost honest?**
Yes. Rejected attempts are paid for and counted, and so is the judge call when the gate is a judge. Grading cost is tracked separately because you don't pay it in production.

**Why include "Fable at low effort" as a config?**
Because a router should have to beat the simple alternative. One strong model with less thinking has no gate, no retries and one prompt cache. If it matches the router on quality and cost, the router isn't worth its complexity.

**How do you know a difference between two models is real?**
Each pass rate has a 95% Wilson interval. With 12 cases the interval is wide, so two configs are only different when their intervals clearly separate. `--reps` and more cases narrow it.

**What stops the eval itself from being wrong?**
Cases test both directions (should call the tool / should not), include boundary values (day 30 vs day 31) and prompt injections in tool data. The harness checks that the model that answered is the model it asked for. Every trial keeps its full transcript, so a surprising score can be read instead of guessed at.

**What would you add next?**
Parallel trials, replay of recorded responses, OpenTelemetry export, and a bigger case set built from real production traces.

## Words to know

| Term | One line |
|---|---|
| Eval | A repeatable test of an AI system's output quality |
| Trace / span | The record of a run / one step in it |
| LLM-as-judge | Using a model to grade another model's answer against a rubric |
| Regression | Something that used to work and now doesn't |
| Baseline | The accepted scores that new runs are compared to |
| Model cascade / router | Try a cheap model first, move up when needed |
| Wilson interval | A confidence interval for a pass rate that behaves well on small samples |
| Structured outputs | Forcing the model's reply to match a JSON schema |

## For the CV

Fill the blanks from a real run (`examples/suite.json` or, better, your own agent). Never use the simulated numbers.

> **AgentGate - evaluation and regression gate for AI agents** (Python, Claude API, SQLite, GitHub Actions)
> Traces every model and tool call with cost and latency; scores answers with code checks and an LLM judge; fails CI on a quality drop.
> On __ cases × __ runs: a Haiku → Sonnet → Fable router reached __% pass rate at __% of the cost of Fable 5.2 alone.

## To get real numbers

1. Set `ANTHROPIC_API_KEY` and run `examples/suite.json` with `--reps 3`.
2. Better: point a suite at one of your own agents. The Second Brain bot already has 66 labelled cases in `tests/fixtures.jsonl`; turning them into AgentGate cases gives you a result on a real project.
3. Save the baseline, push, and add the `ANTHROPIC_API_KEY` secret so the CI job can run.
