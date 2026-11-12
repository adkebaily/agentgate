"""Trace: the one object an agent uses to call the model and its tools.

Every call is recorded as a span (model, tokens, cost, latency, tool input and
output), so a run can be scored, priced and read back later.
"""

import time

from . import pricing


class Trace:
    def __init__(self, client, model, effort=None, max_tokens=16000, timeout=300):
        self.client, self.model, self.effort = client, model, effort
        self.max_tokens, self.timeout = max_tokens, timeout
        self.spans = []

    def llm(self, kind="llm", **kwargs):
        """Call client.messages.create with this trace's model and record it."""
        kwargs.setdefault("max_tokens", self.max_tokens)
        if self.effort:
            kwargs["output_config"] = {**kwargs.get("output_config", {}), "effort": self.effort}
        start = time.perf_counter()
        response = self.client.messages.create(model=self.model, timeout=self.timeout, **kwargs)
        ms = (time.perf_counter() - start) * 1000
        # A score from a different model than the one asked for measures nothing.
        if not response.model.startswith(self.model):
            raise RuntimeError(f"asked for {self.model}, served by {response.model}")
        self.spans.append({
            "kind": kind,
            "model": self.model,
            "ms": ms,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost": pricing.cost(self.model, response.usage),
            "stop_reason": response.stop_reason,
            "text": "".join(b.text for b in response.content if b.type == "text"),
            "tool_calls": [{"name": b.name, "input": b.input} for b in response.content if b.type == "tool_use"],
        })
        return response

    def tool(self, name, fn, **tool_input):
        """Run a tool and record it. Returns (output, is_error) for the tool_result block."""
        start = time.perf_counter()
        try:
            if fn is None:
                raise LookupError(f"unknown tool {name}")
            output, is_error = str(fn(**tool_input)), False
        except Exception as e:  # the model must see the failure to recover from it
            output, is_error = f"Error: {e}", True
        self.spans.append({
            "kind": "tool", "name": name, "input": tool_input, "output": output,
            "is_error": is_error, "ms": (time.perf_counter() - start) * 1000, "cost": 0.0,
        })
        return output, is_error


def render(spans):
    """Spans as a readable transcript, for the judge and for `agentgate show`."""
    lines = []
    for s in spans:
        if s["kind"] == "tool":
            lines.append(f"  tool {s['name']}({s['input']}) -> {s['output']}")
            continue
        head = f"[{s['kind']} {s['model']}] {s['input_tokens']}+{s['output_tokens']} tok, ${s['cost']:.5f}, {s['ms']:.0f} ms, stop={s['stop_reason']}"
        lines.append(head)
        for call in s["tool_calls"]:
            lines.append(f"  wants {call['name']}({call['input']})")
        if s["text"]:
            lines.append(f"  says: {s['text']}")
    return "\n".join(lines)
