from pathlib import Path
import pytest
from refund_resolution.agent import Proposal, propose
from refund_resolution.service import resolve
from refund_resolution.workspace import Workspace

FIXTURE = Path(__file__).parents[1] / "fixtures" / "starter.json"


def state(tmp_path):
    path = tmp_path / "workspace.json"
    path.write_text(FIXTURE.read_text())
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
    result = resolve("t-100", "approval-100", workspace, "anthropic:test",
                     lambda *_: pytest.fail("no second model call"))
    assert result["status"] == "already_refunded"
    assert workspace.data["tickets"][0]["status"] == "refunded"
    assert workspace.data["payments"][0]["refunded_cents"] == 4999


def test_unknown_ticket_and_model_key_boundary(tmp_path, monkeypatch):
    workspace = state(tmp_path)
    with pytest.raises(ValueError, match="unknown ticket"):
        resolve("missing", None, workspace, "anthropic:test", proposal())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        propose("t-100", workspace, "anthropic:claude-sonnet-4-6")
