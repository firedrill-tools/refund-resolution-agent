"""Enforce approval, amount, identity, and idempotency after model proposal."""
import asyncio
from copy import deepcopy
import inspect
from threading import Lock
from .agent import Proposal, propose_async
from .evidence import Receipt
from .workspace import Workspace


_active_workspaces = set()
_claim_lock = Lock()


def resolve(ticket_id: str, approval_id: str | None, workspace: Workspace,
            model: str, proposer=None, **options) -> dict:
    """Synchronous entry point. Async applications use resolve_async."""
    return asyncio.run(resolve_async(ticket_id, approval_id, workspace, model, proposer, **options))


async def resolve_async(ticket_id: str, approval_id: str | None, workspace: Workspace,
                        model: str, proposer=None, *, output_root=None, transport=None, timeout_seconds=120) -> dict:
    path = workspace.path.resolve(strict=True)
    with _claim_lock:
        if path in _active_workspaces:
            raise RuntimeError("workspace already has an active refund resolution")
        _active_workspaces.add(path)
    receipt = None; result = None; failure = None
    previous_observer = workspace.observer
    try:
        workspace.reload()
        receipt = Receipt(workspace, ticket_id, approval_id, model, output_root)
        def observe(name, args, value):
            receipt.action(name,args,value)
            if previous_observer: previous_observer(name,args,value)
        workspace.observer = observe
        async def plan(*args):
            if proposer is not None:
                value = proposer(*args)
                return await value if inspect.isawaitable(value) else value
            return await propose_async(*args, transport=transport, on_observation=receipt.observations, timeout_seconds=timeout_seconds)
        result = await _resolve(ticket_id,approval_id,workspace,model,plan)
        workspace.reload()
        result.update(run_id=receipt.data['run_id'],receipt_path=str(receipt.path))
    except BaseException as error:
        failure = error
        if receipt and hasattr(error,'observations'):
            receipt.data['provider_observations'] = deepcopy(error.observations)
    finally:
        workspace.observer = previous_observer
        with _claim_lock: _active_workspaces.discard(path)
        if receipt:
            try: receipt.finish(result,failure)
            except BaseException as error:
                receipt.data.update(status='failed',evidence_error=f'{type(error).__name__}: {error}')
                if failure is None: failure=error
    if failure is not None:
        failure.evidence = receipt.data if receipt else None
        failure.receipt_path = str(receipt.path) if receipt else None
        raise failure
    return result


async def _resolve(ticket_id, approval_id, workspace, model, proposer):
    workspace.reload()
    ticket = workspace.get("tickets", ticket_id)
    if ticket is None:
        raise ValueError("unknown ticket")
    previous = workspace.prior_refund(ticket_id)
    if previous:
        return recover(ticket, previous, workspace)
    if ticket["status"] != "open":
        return {"status": "review", "reason": "ticket is not open", "model_usage": None}
    proposal, usage = await proposer(ticket_id, workspace, model)
    await asyncio.sleep(0)  # Deliver pending cancellation before financial effects.
    if not isinstance(proposal, Proposal):
        raise ValueError("proposer did not return a validated Proposal")
    workspace.reload()
    current = workspace.get("tickets", ticket_id)
    if current != ticket:
        return {"status": "review", "reason": "ticket changed during planning; review current request", "model_usage": usage}
    previous = workspace.prior_refund(ticket_id)
    if previous:
        result = recover(current, previous, workspace)
        result["model_usage"] = usage
        return result
    if proposal.action != "refund":
        return {"status": proposal.action, "reason": proposal.reason, "model_usage": usage}
    order = workspace.get("orders", ticket.get("order_id", ""))
    if order is None:
        return {"status": "review", "reason": "order is missing", "model_usage": usage}
    payment = workspace.payment_for(order["id"])
    approval = workspace.approval(approval_id) if approval_id else None
    problems = []
    if order.get("status") != "delivered":
        problems.append("order is not delivered")
    ticket_email = ticket.get("customer_email"); order_email = order.get("customer_email")
    if not isinstance(ticket_email, str) or not isinstance(order_email, str) or not ticket_email or ticket_email.casefold() != order_email.casefold():
        problems.append("ticket and order customer differ")
    if not payment:
        problems.append("one unambiguous payment is required")
    elif not order.get("currency") or payment.get("currency") != order["currency"]:
        problems.append("payment and order currency differ or payment currency is unknown")
    if not approval or approval["status"] != "approved":
        problems.append("approval missing")
    elif type(approval.get("amount_cents")) is not int or approval["amount_cents"] <= 0:
        problems.append("approval amount must be positive integer cents")
    elif (approval.get("ticket_id"), approval.get("order_id"), approval.get("currency"), approval["amount_cents"]) != (
            ticket_id, order["id"], order.get("currency"), proposal.amount_cents):
        problems.append("approval does not match ticket, order, currency and exact amount")
    valid_payment = payment is not None and all(type(payment.get(field)) is int and payment[field] >= 0
                                               for field in ("captured_cents", "refunded_cents"))
    if payment and (not valid_payment or payment["refunded_cents"] > payment["captured_cents"]):
        valid_payment = False
        problems.append("payment amounts are invalid")
    if valid_payment and (proposal.amount_cents <= 0 or proposal.amount_cents > payment["captured_cents"] - payment["refunded_cents"]):
        problems.append("refund exceeds remaining captured amount")
    if problems:
        return {"status": "review", "reason": "; ".join(problems), "proposal": proposal.model_dump(),
                "model_usage": usage}
    refund = workspace.issue_refund(ticket_id, order["id"], proposal.amount_cents, approval_id,
        {"requested_model": model, "proposal": proposal.model_dump(), "usage": usage})
    workspace.close_ticket(ticket_id, refund["id"])
    return {"status": "refunded", "refund": refund, "model_usage": usage}


def recover(ticket: dict, previous: dict, workspace: Workspace) -> dict:
    order = workspace.get("orders", ticket.get("order_id", ""))
    payment = workspace.get("payments", previous.get("payment_id", ""))
    original = previous.get("authorized_ticket")
    same_intent = isinstance(original, dict) and all(original.get(key) == ticket.get(key) for key in ("id", "order_id", "customer_email", "message"))
    if (not same_intent or ticket.get("status") not in {"open", "refunded"} or order is None
            or (ticket.get("status") == "refunded" and ticket.get("refund_id") != previous.get("id"))
            or previous.get("order_id") != ticket.get("order_id")
            or ticket["customer_email"].casefold() != order["customer_email"].casefold()
            or payment is None or payment["order_id"] != previous["order_id"]
            or previous.get("currency") != order["currency"] or payment.get("currency") != order["currency"]
            or type(previous.get("amount_cents")) is not int or previous["amount_cents"] <= 0
            or type(payment.get("refunded_cents")) is not int
            or payment["refunded_cents"] < previous["amount_cents"]):
        return {"status": "review", "reason": "recorded refund conflicts with the current ticket; no new payment attempted",
                "refund": previous, "model_usage": None}
    workspace.close_ticket(ticket["id"], previous["id"])
    return {"status": "already_refunded", "refund": previous, "model_usage": None}
