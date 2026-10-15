"""Enforce approval, amount, identity, and idempotency after model proposal."""
from .agent import Proposal, propose
from .workspace import Workspace


def resolve(ticket_id: str, approval_id: str | None, workspace: Workspace,
            model: str, proposer=propose) -> dict:
    ticket = workspace.get("tickets", ticket_id)
    if ticket is None:
        raise ValueError("unknown ticket")
    previous = workspace.prior_refund(ticket_id)
    if previous:
        workspace.close_ticket(ticket_id, previous["id"])
        return {"status": "already_refunded", "refund": previous, "model_usage": None}
    proposal, usage = proposer(ticket_id, workspace, model)
    if proposal.action != "refund":
        return {"status": proposal.action, "reason": proposal.reason, "model_usage": usage}
    order = workspace.get("orders", ticket["order_id"])
    payment = workspace.payment_for(order["id"])
    approval = workspace.approval(approval_id) if approval_id else None
    problems = []
    if order["status"] != "delivered":
        problems.append("order is not delivered")
    if ticket["customer_email"].casefold() != order["customer_email"].casefold():
        problems.append("ticket and order customer differ")
    if not approval or approval["status"] != "approved":
        problems.append("approval missing")
    elif (approval["ticket_id"], approval["order_id"], approval["currency"], approval["amount_cents"]) != (
            ticket_id, order["id"], order["currency"], proposal.amount_cents):
        problems.append("approval does not match ticket, order, currency and exact amount")
    if not payment or proposal.amount_cents <= 0 or proposal.amount_cents > payment["captured_cents"] - payment["refunded_cents"]:
        problems.append("refund exceeds remaining captured amount")
    if problems:
        return {"status": "review", "reason": "; ".join(problems), "proposal": proposal.model_dump(),
                "model_usage": usage}
    refund = workspace.issue_refund(ticket_id, order["id"], proposal.amount_cents, approval_id)
    workspace.close_ticket(ticket_id, refund["id"])
    return {"status": "refunded", "refund": refund, "model_usage": usage}
