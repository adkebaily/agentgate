"""A scripted stand-in for the Claude client, so the demo and the tests run offline for free.

It is NOT a model and its numbers say nothing about any real model. Two fake
tiers exist to exercise the router:
  sim-large  always reads the policy and applies it correctly.
  sim-small  is lazy on even-numbered orders: it skips the policy and says ELIGIBLE.
"""

import json
import re
from datetime import date
from types import SimpleNamespace as NS

TODAY = date(2026, 11, 1)


def _text(value):
    return [NS(type="text", text=value)]


class SimClient:
    def __init__(self):
        self.messages = self  # so client.messages.create(...) works

    def create(self, *, model, messages, **_):
        task = messages[0]["content"]
        names, results = {}, {}  # tool_use id -> tool name, tool name -> result
        for m in messages[1:]:
            for block in m["content"]:
                if getattr(block, "type", None) == "tool_use":
                    names[block.id] = block.name
                elif isinstance(block, dict) and block["type"] == "tool_result":
                    results[names[block["tool_use_id"]]] = block["content"]

        order_id = re.search(r"\b[A-Z]\d{3}\b", task)
        stop = "end_turn"
        if results:
            content = _text(self._decide(json.loads(results["get_order"]), "get_refund_policy" in results))
        elif order_id:
            lazy = model == "sim-small" and int(order_id[0][1:]) % 2 == 0
            content = [NS(type="tool_use", id="t1", name="get_order", input={"order_id": order_id[0]})]
            if not lazy:
                content.append(NS(type="tool_use", id="t2", name="get_refund_policy", input={}))
            stop = "tool_use"
        elif "refund" in task.lower():
            content = _text("Could you share your order number so I can check?")
        else:
            content = _text("We are open Monday to Friday, 9:00 to 18:00.")

        said = json.dumps([getattr(b, "text", None) or getattr(b, "input", "") for b in content])
        usage = NS(input_tokens=len(str(messages)) // 4, output_tokens=len(said) // 4)
        return NS(model=model, content=content, stop_reason=stop, usage=usage)

    @staticmethod
    def _decide(order, read_policy):
        if "error" in order:
            return "NOT FOUND - I could not find that order."
        if read_policy:
            if order["status"] != "delivered":
                return "NOT ELIGIBLE - the order has not been delivered yet."
            if order["final_sale"]:
                return "NOT ELIGIBLE - final sale items cannot be refunded."
            if (TODAY - date.fromisoformat(order["delivered_on"])).days > 30:
                return "NOT ELIGIBLE - the 30-day refund window has passed."
        return f"ELIGIBLE - refund of {order['total']:.2f} euros."
