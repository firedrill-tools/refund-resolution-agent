from pathlib import Path
import json
import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from refund_resolution import agent as agent_module
from refund_resolution.agent import Proposal, propose
from refund_resolution.service import resolve
from refund_resolution.workspace import Workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "starter.json"


def state(tmp_path, fixture=FIXTURE):
    path = tmp_path / "workspace.json"
    path.write_text(fixture.read_text())
    return Workspace(path)


def proposal(amount=4999, action="refund"):
    return lambda *_: (Proposal(action=action, amount_cents=amount, reason="Damaged item reported"), {"requests": 1})


def test_approved_partial_refund_and_retry(tmp_path):
    workspace = state(tmp_path)
    first = resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    assert first["status"] == "refunded"
    assert workspace.data["payments"][0]["refunded_cents"] == 4999
    assert workspace.data["tickets"][0]["refund_id"] == first["refund"]["id"]
    second = resolve("t-100", "approval-100", workspace, "anthropic:test",
                     lambda *_: pytest.fail("model must not be called twice"))
    assert second["status"] == "already_refunded"
    assert second["model_usage"] is None
    assert second["refund"]["proposal_observation"]["usage"] == {"requests": 1}
    assert len(workspace.data["refunds"]) == 1


def test_missing_or_wrong_approval_does_not_pay(tmp_path):
    workspace = state(tmp_path)
    assert resolve("t-100", None, workspace, "anthropic:test", proposal())["status"] == "review"
    assert resolve("t-100", "approval-100", workspace, "anthropic:test", proposal(5000))["status"] == "review"
    assert workspace.data["payments"][0]["refunded_cents"] == 0


def test_processing_order_and_limit_stop_refund(tmp_path):
    workspace = state(tmp_path)
    assert resolve("t-102", None, workspace, "anthropic:test", proposal(6500))["status"] == "review"
    workspace.data["approvals"].append({"id": "approval-bad", "ticket_id": "t-101",
        "order_id": "ord-101", "amount_cents": 7000, "currency": "USD", "status": "approved"})
    workspace.save()
    assert resolve("t-101", "approval-bad", workspace, "anthropic:test", proposal(7000))["status"] == "review"
    assert workspace.data["payments"][1]["refunded_cents"] == 2000


def test_retry_after_ticket_update_failure(tmp_path):
    workspace = state(tmp_path)
    original = workspace.close_ticket
    attempts = []
    def fail_once(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("ticket unavailable")
        return original(*args)
    workspace.close_ticket = fail_once
    with pytest.raises(RuntimeError, match="ticket unavailable"):
        resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    restarted = Workspace(workspace.path)
    result = resolve("t-100", "approval-100", restarted, "anthropic:test",
                     lambda *_: pytest.fail("no second model call"))
    assert result["status"] == "already_refunded"
    assert restarted.data["tickets"][0]["status"] == "refunded"
    assert restarted.data["payments"][0]["refunded_cents"] == 4999


def test_unknown_ticket_and_model_key_boundary(tmp_path, monkeypatch):
    workspace = state(tmp_path)
    with pytest.raises(ValueError, match="unknown ticket"):
        resolve("missing", None, workspace, "anthropic:test", proposal())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        propose("t-100", workspace, "anthropic:claude-sonnet-4-6")


def test_payment_currency_must_match_the_order_and_approval(tmp_path):
    workspace = state(tmp_path)
    workspace.data["payments"][0]["currency"] = "CAD"
    workspace.save()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    assert result["status"] == "review"
    assert workspace.data["refunds"] == []
    assert workspace.data["payments"][0]["refunded_cents"] == 0


def test_ticket_correction_during_model_planning_invalidates_old_approval(tmp_path):
    workspace = state(tmp_path)
    def corrected(*_args):
        ticket = workspace.data["tickets"][0]
        ticket["order_id"] = "ord-101"
        ticket["customer_email"] = "leo@northstar.example"
        workspace.save()
        return proposal()()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", corrected)
    assert result["status"] == "review"
    assert workspace.data["refunds"] == []
    assert workspace.data["payments"][0]["refunded_cents"] == 0


def test_closed_ticket_cannot_authorize_a_new_refund(tmp_path):
    workspace = state(tmp_path)
    workspace.data["tickets"][0]["status"] = "cancelled"
    workspace.save()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    assert result["status"] == "review"
    assert workspace.data["refunds"] == []


def test_multiple_capture_records_cannot_silently_choose_the_first_payment(tmp_path):
    workspace = state(tmp_path)
    workspace.data["payments"].append({"id": "pay-100-b", "order_id": "ord-100", "captured_cents": 5000,
                                       "refunded_cents": 0, "currency": "USD"})
    workspace.save()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    assert result["status"] == "review"
    assert workspace.data["refunds"] == []


def test_actual_pydantic_agent_reads_only_the_assigned_case_and_records_reported_usage(tmp_path, monkeypatch):
    workspace = state(tmp_path); turns = []
    def model(messages, info):
        turns.append(messages)
        usage = RequestUsage(input_tokens=40, output_tokens=15)
        if len(turns) == 1:
            return ModelResponse([
                ToolCallPart("read_ticket", {"requested_ticket_id": "t-100"}, tool_call_id="ticket"),
                ToolCallPart("read_order", {"order_id": "ord-100"}, tool_call_id="order"),
                ToolCallPart("read_payment", {"order_id": "ord-100"}, tool_call_id="payment"),
                ToolCallPart("read_ticket", {"requested_ticket_id": "t-101"}, tool_call_id="other-ticket"),
                ToolCallPart("read_order", {"order_id": "ord-101"}, tool_call_id="other-order"),
                ToolCallPart("read_payment", {"order_id": "ord-101"}, tool_call_id="other-payment"),
            ], usage=usage, model_name="scripted-response-model")
        returned = {part.tool_call_id: json.loads(part.content) for part in messages[-1].parts if isinstance(part, ToolReturnPart)}
        assert returned["ticket"]["id"] == "t-100" and returned["order"]["id"] == "ord-100"
        assert returned["payment"]["payments"][0]["currency"] == "USD"
        for key in ("other-ticket", "other-order", "other-payment"):
            assert list(returned[key]) == ["error"]
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {
            "action": "refund", "amount_cents": 4999, "reason": "Synthetic damaged item"}, tool_call_id="proposal")],
            usage=usage, model_name="scripted-response-model")
    build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda ticket_id, _requested: build(ticket_id, FunctionModel(model, model_name="scripted-response-model")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-not-a-provider-key")
    result, usage = propose("t-100", workspace, "anthropic:scripted-request-model")
    assert result.amount_cents == 4999 and len(turns) == 2
    assert usage == {"requests": 2, "input_tokens": 80, "output_tokens": 30, "total_tokens": 110,
                     "requested_model": "anthropic:scripted-request-model", "response_model": "scripted-response-model", "cost_usd": None}
    assert workspace.data["refunds"] == []


@pytest.mark.parametrize("amount", [True, "4999", 4999.0])
def test_proposal_requires_integer_cents(amount):
    with pytest.raises(ValidationError):
        Proposal(action="refund", amount_cents=amount, reason="Synthetic damaged item")


@pytest.mark.parametrize("field,value", [("approval", True), ("captured", True), ("refunded", -1)])
def test_invalid_stored_money_does_not_authorize_a_payment(tmp_path, field, value):
    workspace = state(tmp_path)
    workspace.data["approvals"][0]["amount_cents"] = 1
    if field == "approval":
        workspace.data["approvals"][0]["amount_cents"] = value
    else:
        workspace.data["payments"][0][field + "_cents"] = value
    workspace.save()
    assert resolve("t-100", "approval-100", workspace, "anthropic:test", proposal(1))["status"] == "review"
    assert workspace.data["refunds"] == []


@pytest.mark.parametrize("change", ["approval_revoked", "balance_changed"])
def test_records_are_reloaded_after_model_planning(tmp_path, change):
    workspace = state(tmp_path)
    def changing(*_args):
        changed = Workspace(workspace.path)
        if change == "approval_revoked":
            changed.data["approvals"][0]["status"] = "revoked"
        else:
            changed.data["payments"][0]["refunded_cents"] = 10000
        changed.save()
        return proposal()()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", changing)
    assert result["status"] == "review"
    assert Workspace(workspace.path).data["refunds"] == []


def test_prior_payment_cannot_close_a_ticket_relinked_to_another_order(tmp_path):
    workspace = state(tmp_path)
    resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    workspace.data["tickets"][0].update(order_id="ord-101", customer_email="leo@northstar.example", status="open")
    workspace.save()
    result = resolve("t-100", "approval-100", workspace, "anthropic:test", lambda *_: pytest.fail("no new model call"))
    assert result["status"] == "review"
    assert workspace.data["tickets"][0]["status"] == "open"
    assert len(workspace.data["refunds"]) == 1


def test_failed_atomic_payment_commit_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    workspace = state(tmp_path); before = workspace.path.read_bytes()
    def failed(*_args):
        raise OSError("synthetic journal replacement failure")
    with monkeypatch.context() as fault:
        fault.setattr("refund_resolution.workspace.os.replace", failed)
        with pytest.raises(OSError, match="replacement failure"):
            resolve("t-100", "approval-100", workspace, "anthropic:test", proposal())
    assert workspace.path.read_bytes() == before
    assert list(tmp_path.glob(".workspace.json.*.tmp")) == []
    restarted = Workspace(workspace.path)
    assert restarted.data["refunds"] == []
    result = resolve("t-100", "approval-100", restarted, "anthropic:test", proposal())
    assert result["status"] == "refunded" and len(restarted.data["refunds"]) == 1


def test_busy_dataset_targets_the_exact_cad_payment_and_preserves_other_records(tmp_path):
    workspace = state(tmp_path, FIXTURE.with_name("busy.json"))
    original_payments = [item.copy() for item in workspace.data["payments"] if item["id"] != "pay-104"]
    original_tickets = [item.copy() for item in workspace.data["tickets"] if item["id"] != "t-104"]
    result = resolve("t-104", "approval-104", workspace, "anthropic:test", proposal(2200))
    assert result["status"] == "refunded"
    assert result["refund"]["currency"] == "CAD" and result["refund"]["payment_id"] == "pay-104"
    assert [item for item in workspace.data["payments"] if item["id"] != "pay-104"] == original_payments
    assert [item for item in workspace.data["tickets"] if item["id"] != "t-104"] == original_tickets


@pytest.mark.parametrize("profile,ticket,approval,amount", [
    ("ambiguous", "t-100", "approval-100", 4999),
    ("boundary", "t-901", "approval-901", 500),
])
def test_ambiguous_and_exhausted_profiles_preserve_payment_state(tmp_path, profile, ticket, approval, amount):
    workspace = state(tmp_path, FIXTURE.with_name(profile + ".json"))
    before = workspace.path.read_bytes()
    assert resolve(ticket, approval, workspace, "anthropic:test", proposal(amount))["status"] == "review"
    assert workspace.path.read_bytes() == before
