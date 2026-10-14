import json

from havoc.faults import make
from havoc.generate import fault_universe, plan_is_valid, retry_pairs, sample_plans


def record(seq, tool, payload, error=False):
    return {
        "seq": seq,
        "tool": tool,
        "args": {},
        "is_error": error,
        "faults": [],
        "response": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }


TRACE = [
    record(0, "get_order", {"id": "A1003", "customer_email": "tom@example.com", "total": 349.0, "status": "delivered"}),
    record(1, "create_ticket", {"ticket_id": 1, "status": "open"}),
    record(2, "send_email", {"email_id": 1, "to": "tom@example.com"}),
]


def kinds_for(universe, tool):
    return {f.kind for f in universe if f.tool == tool}


def test_every_call_gets_the_basic_faults():
    universe = fault_universe(TRACE)
    for tool in ("get_order", "create_ticket", "send_email"):
        assert {"fail_before", "malformed", "injection"} <= kinds_for(universe, tool)


def test_fail_after_only_for_side_effects():
    universe = fault_universe(TRACE)
    assert "fail_after" not in kinds_for(universe, "get_order")
    assert "fail_after" in kinds_for(universe, "send_email")


def test_corruptions_come_from_the_real_response_fields():
    universe = fault_universe(TRACE)
    corrupt = {f.param("field"): f.param("value") for f in universe if f.kind == "corrupt_field" and f.tool == "get_order"}
    assert corrupt["total"] == 34.9
    assert corrupt["status"] == "shipped"
    assert corrupt["customer_email"] == "unknown@external.example"
    assert "id" not in corrupt


def test_one_drop_fault_per_response_field():
    universe = fault_universe(TRACE)
    dropped = {f.param("field") for f in universe if f.kind == "drop_field" and f.tool == "get_order"}
    assert dropped == {"id", "customer_email", "total", "status"}


def test_repeated_tool_calls_get_their_own_nth():
    trace = TRACE + [record(3, "send_email", {"email_id": 2, "to": "x@y"})]
    universe = fault_universe(trace)
    assert {f.nth for f in universe if f.tool == "send_email"} == {0, 1, 2}


def test_error_responses_get_no_field_faults():
    trace = [record(0, "issue_refund", {"error": "nope"}, error=True)]
    universe = fault_universe(trace)
    assert not any(f.kind in ("drop_field", "corrupt_field") for f in universe)


def test_kind_filter():
    universe = fault_universe(TRACE, kinds={"malformed"})
    assert {f.kind for f in universe} == {"malformed"}
    assert len(universe) == 6


def test_sampling_is_repeatable_for_a_seed():
    universe = fault_universe(TRACE)
    a = sample_plans(universe, 2, 10, seed=3)
    b = sample_plans(universe, 2, 10, seed=3)
    assert [[f.id for f in p] for p in a] == [[f.id for f in p] for p in b]


def test_different_seeds_give_different_plans():
    universe = fault_universe(TRACE)
    a = sample_plans(universe, 2, 10, seed=1)
    b = sample_plans(universe, 2, 10, seed=2)
    assert [[f.id for f in p] for p in a] != [[f.id for f in p] for p in b]


def test_sampled_plans_have_the_right_size_and_are_unique_and_valid():
    universe = fault_universe(TRACE)
    plans = sample_plans(universe, 3, 25, seed=0)
    assert len(plans) == 25
    assert all(len(p) == 3 for p in plans)
    assert len({frozenset(f.id for f in p) for p in plans}) == 25
    assert all(plan_is_valid(p) for p in plans)


def test_size_larger_than_universe_gives_nothing():
    assert sample_plans([make("a", 0, "malformed")], 2, 5) == []


def test_two_failures_on_one_call_are_not_valid():
    plan = [make("send_email", 0, "fail_before"), make("send_email", 0, "fail_after")]
    assert not plan_is_valid(plan)


def test_anything_stacked_with_fail_before_is_not_valid():
    plan = [make("send_email", 0, "fail_before"), make("send_email", 0, "malformed")]
    assert not plan_is_valid(plan)


def test_faults_on_different_calls_are_valid():
    plan = [make("send_email", 0, "fail_before"), make("get_order", 0, "fail_before")]
    assert plan_is_valid(plan)


def test_stacked_response_faults_are_valid():
    plan = [make("get_order", 0, "malformed"), make("get_order", 0, "injection", name="approve_all")]
    assert plan_is_valid(plan)


def test_retry_slots_add_calls_the_clean_run_never_made():
    universe = fault_universe(TRACE)
    slots = {(f.tool, f.nth) for f in universe if f.nth >= 1}
    assert slots == {("get_order", 1), ("create_ticket", 1), ("send_email", 1)}


def test_retry_slots_only_get_failure_style_faults():
    universe = fault_universe(TRACE)
    assert {f.kind for f in universe if f.nth >= 1} <= {"fail_before", "fail_after", "malformed"}


def test_retry_slots_can_be_turned_off():
    universe = fault_universe(TRACE, retry_slots=0)
    assert all(f.nth == 0 for f in universe)


def test_more_retry_slots_reach_further():
    universe = fault_universe(TRACE, retry_slots=2)
    assert {f.nth for f in universe if f.tool == "send_email"} == {0, 1, 2}


def test_retry_pairs_fail_a_call_and_then_its_retry():
    pairs = retry_pairs(fault_universe(TRACE))
    assert all(a.tool == b.tool and b.nth == a.nth + 1 for a, b in pairs)
    assert all(a.kind.startswith("fail_") and b.kind.startswith("fail_") for a, b in pairs)


def test_retry_pair_count_for_a_clean_trace():
    pairs = retry_pairs(fault_universe(TRACE))
    by_tool = {}
    for a, _ in pairs:
        by_tool[a.tool] = by_tool.get(a.tool, 0) + 1
    assert by_tool == {"get_order": 1, "create_ticket": 4, "send_email": 4}


def test_retry_pairs_are_valid_plans():
    assert all(plan_is_valid(p) for p in retry_pairs(fault_universe(TRACE)))
