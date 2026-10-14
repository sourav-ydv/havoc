import json
import random

from havoc.faults import INJECTIONS, make

SIDE_EFFECT_TOOLS = {"issue_refund", "send_email", "create_ticket", "close_ticket"}


def response_json(record):
    response = record.get("response") or {}
    for block in response.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                data = json.loads(block.get("text", ""))
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def corruptions(key, value):
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [round(value * 0.1, 2)]
    if isinstance(value, str) and "@" in value:
        return ["unknown@external.example"]
    if key == "status":
        return ["shipped" if value == "delivered" else "delivered"]
    return []


def basic_faults(tool, nth):
    found = [make(tool, nth, "fail_before")]
    if tool in SIDE_EFFECT_TOOLS:
        found.append(make(tool, nth, "fail_after"))
    found.append(make(tool, nth, "malformed"))
    return found


def fault_universe(trace, kinds=None, retry_slots=1):
    seen = {}
    found = []

    for record in sorted(trace, key=lambda r: r["seq"]):
        tool = record["tool"]
        nth = seen.get(tool, 0)
        seen[tool] = nth + 1

        found.extend(basic_faults(tool, nth))

        data = response_json(record) if not record["is_error"] else None
        if data:
            for key in data:
                found.append(make(tool, nth, "drop_field", field=key))
            for key, value in data.items():
                for bad in corruptions(key, value):
                    found.append(make(tool, nth, "corrupt_field", field=key, value=bad))

        for name in sorted(INJECTIONS):
            found.append(make(tool, nth, "injection", name=name))

    for tool, count in seen.items():
        for extra in range(count, count + retry_slots):
            found.extend(basic_faults(tool, extra))

    if kinds:
        found = [f for f in found if f.kind in kinds]
    return found


def plan_is_valid(plan):
    by_call = {}
    for fault in plan:
        by_call.setdefault((fault.tool, fault.nth), []).append(fault)
    for faults in by_call.values():
        kinds = [f.kind for f in faults]
        if sum(k.startswith("fail_") for k in kinds) > 1:
            return False
        if "fail_before" in kinds and len(faults) > 1:
            return False
    return True


def sample_plans(universe, size, count, seed=0):
    rng = random.Random(seed)
    if size > len(universe):
        return []
    plans, seen = [], set()
    attempts = 0
    while len(plans) < count and attempts < count * 50:
        attempts += 1
        plan = rng.sample(universe, size)
        key = frozenset(f.id for f in plan)
        if key in seen or not plan_is_valid(plan):
            continue
        seen.add(key)
        plans.append(sorted(plan, key=lambda f: (f.tool, f.nth, f.kind)))
    return plans


def retry_pairs(universe):
    failing = [f for f in universe if f.kind.startswith("fail_")]
    pairs = []
    for first in failing:
        for second in failing:
            if second.tool == first.tool and second.nth == first.nth + 1:
                pairs.append([first, second])
    return pairs
