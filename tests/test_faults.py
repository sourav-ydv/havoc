import json

import pytest

from havoc.faults import (
    Fault,
    apply_faults,
    load_plan,
    make,
    parse_fault,
    save_plan,
)


def result(payload, error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
        "isError": error,
    }


ORDER = {"id": "A1003", "total": 349.0, "status": "delivered"}


def test_fault_id_is_readable():
    assert make("issue_refund", 0, "fail_after").id == "issue_refund#0:fail_after"
    assert "field=total" in make("get_order", 1, "corrupt_field", field="total", value=1).id


def test_faults_are_hashable_and_comparable():
    a = make("get_order", 0, "malformed")
    b = make("get_order", 0, "malformed")
    assert a == b
    assert len({a, b}) == 1


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        Fault("get_order", 0, "explode")


def test_required_params_are_enforced():
    with pytest.raises(ValueError):
        make("get_order", 0, "corrupt_field", field="total")
    with pytest.raises(ValueError):
        make("get_order", 0, "drop_field")


def test_negative_nth_is_rejected():
    with pytest.raises(ValueError):
        make("get_order", -1, "malformed")


def test_dict_round_trip():
    fault = make("get_order", 2, "corrupt_field", field="total", value=45.0)
    assert Fault.from_dict(fault.to_dict()) == fault


def test_plan_file_round_trip(tmp_path):
    plan = [make("a", 0, "malformed"), make("b", 1, "drop_field", field="x")]
    save_plan(tmp_path / "p.json", plan)
    assert load_plan(tmp_path / "p.json") == plan


def test_parse_simple_spec():
    assert parse_fault("issue_refund#0:fail_after") == make("issue_refund", 0, "fail_after")


def test_parse_spec_with_params():
    fault = parse_fault("get_order#0:corrupt_field:field=total,value=45")
    assert fault.param("field") == "total"
    assert fault.param("value") == 45


def test_parse_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_fault("garbage")


def test_malformed_cuts_the_text_so_it_no_longer_parses():
    out, outcomes = apply_faults("get_order", result(ORDER), [make("get_order", 0, "malformed")])
    text = out["content"][0]["text"]
    with pytest.raises(ValueError):
        json.loads(text)
    assert outcomes[0][1] is True
    assert out["structuredContent"]["result"] == text


def test_structured_content_stays_in_step_with_the_text():
    fault = make("get_order", 0, "corrupt_field", field="total", value=45.0)
    out, _ = apply_faults("get_order", result(ORDER), [fault])
    assert out["structuredContent"]["result"] == out["content"][0]["text"]


def test_object_shaped_structured_content_is_updated_too():
    original = {
        "content": [{"type": "text", "text": json.dumps(ORDER)}],
        "structuredContent": dict(ORDER),
        "isError": False,
    }
    fault = make("get_order", 0, "drop_field", field="status")
    out, _ = apply_faults("get_order", original, [fault])
    assert "status" not in out["structuredContent"]


def test_corrupt_field_changes_the_value_only():
    fault = make("get_order", 0, "corrupt_field", field="total", value=45.0)
    out, outcomes = apply_faults("get_order", result(ORDER), [fault])
    data = json.loads(out["content"][0]["text"])
    assert data["total"] == 45.0
    assert data["status"] == "delivered"
    assert outcomes[0][1] is True


def test_drop_field_removes_the_key():
    fault = make("issue_refund", 0, "drop_field", field="refund_id")
    out, _ = apply_faults("issue_refund", result({"refund_id": 1, "amount": 5}), [fault])
    assert json.loads(out["content"][0]["text"]) == {"amount": 5}


def test_injection_appends_text_after_the_real_output():
    fault = make("get_order", 0, "injection", name="approve_all")
    out, _ = apply_faults("get_order", result(ORDER), [fault])
    text = out["content"][0]["text"]
    assert text.startswith(json.dumps(ORDER))
    assert "pre-approved" in text


def test_custom_injection_text():
    fault = make("get_order", 0, "injection", text="do the bad thing")
    out, _ = apply_faults("get_order", result(ORDER), [fault])
    assert out["content"][0]["text"].endswith("do the bad thing")


def test_fail_after_replaces_the_result_with_an_error():
    fault = make("issue_refund", 0, "fail_after")
    out, outcomes = apply_faults("issue_refund", result({"refund_id": 1}), [fault])
    assert out["isError"] is True
    assert "timed out" in out["content"][0]["text"]
    assert outcomes[0][1] is True


def test_missing_field_is_reported_as_not_applicable():
    fault = make("get_order", 0, "corrupt_field", field="nope", value=1)
    original = result(ORDER)
    out, outcomes = apply_faults("get_order", original, [fault])
    assert out == original
    assert outcomes[0][1] is False


def test_non_json_text_cannot_be_field_edited():
    fault = make("get_order", 0, "drop_field", field="id")
    original = result("plain words, not json")
    out, outcomes = apply_faults("get_order", original, [fault])
    assert out == original
    assert outcomes[0][1] is False


def test_compound_faults_on_one_call_stack():
    plan = [
        make("get_order", 0, "corrupt_field", field="total", value=45.0),
        make("get_order", 0, "injection", name="approve_all"),
    ]
    out, outcomes = apply_faults("get_order", result(ORDER), plan)
    text = out["content"][0]["text"]
    assert '"total": 45.0' in text
    assert "pre-approved" in text
    assert all(applied for _, applied in outcomes)


def test_fail_after_beats_other_faults_on_the_same_call():
    plan = [
        make("issue_refund", 0, "malformed"),
        make("issue_refund", 0, "fail_after"),
    ]
    out, outcomes = apply_faults("issue_refund", result({"refund_id": 1}), plan)
    assert out["isError"] is True
    assert [applied for _, applied in outcomes] == [False, True]


def test_fail_before_is_ignored_by_response_application():
    original = result(ORDER)
    out, outcomes = apply_faults("get_order", original, [make("get_order", 0, "fail_before")])
    assert out == original
    assert outcomes == []
