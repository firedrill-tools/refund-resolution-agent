"""Synthetic order, payment and ticket services backed by one local file."""
import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile


class Workspace:
    def __init__(self, path: Path):
        self.path = path
        self.reload()

    def reload(self):
        self.data = json.loads(self.path.read_text())

    def get(self, kind: str, item_id: str):
        return next((deepcopy(item) for item in self.data[kind] if item["id"] == item_id), None)

    def payments_for(self, order_id: str):
        return [deepcopy(item) for item in self.data["payments"] if item["order_id"] == order_id]

    def payment_for(self, order_id: str):
        matches = self.payments_for(order_id)
        return matches[0] if len(matches) == 1 else None

    def approval(self, approval_id: str):
        return self.get("approvals", approval_id)

    def prior_refund(self, ticket_id: str):
        return next((deepcopy(r) for r in self.data["refunds"] if r["ticket_id"] == ticket_id), None)

    def issue_refund(self, ticket_id: str, order_id: str, amount_cents: int, approval_id: str,
                     proposal_observation: dict | None = None):
        previous = self.prior_refund(ticket_id)
        if previous:
            return previous
        matched = self.payment_for(order_id)
        if matched is None:
            raise ValueError("one unambiguous payment is required")
        payment = next(p for p in self.data["payments"] if p["id"] == matched["id"])
        if amount_cents <= 0 or amount_cents > payment["captured_cents"] - payment["refunded_cents"]:
            raise ValueError("refund exceeds remaining captured amount")
        refund = {"id": f"refund-{len(self.data['refunds']) + 1:03d}", "ticket_id": ticket_id,
                  "order_id": order_id, "payment_id": payment["id"], "currency": payment["currency"],
                  "amount_cents": amount_cents, "approval_id": approval_id,
                  "proposal_observation": proposal_observation}
        payment["refunded_cents"] += amount_cents
        self.data["refunds"].append(refund)
        self.save()
        return refund

    def close_ticket(self, ticket_id: str, refund_id: str):
        ticket = next(t for t in self.data["tickets"] if t["id"] == ticket_id)
        refund = next(r for r in self.data["refunds"] if r["id"] == refund_id)
        if refund["ticket_id"] != ticket_id or refund["order_id"] != ticket["order_id"]:
            raise ValueError("refund does not match the current ticket")
        ticket["status"] = "refunded"
        ticket["refund_id"] = refund_id
        self.save()

    def save(self):
        temporary_path = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf8", dir=self.path.parent,
                                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as staged:
                temporary_path = Path(staged.name)
                json.dump(self.data, staged, indent=2)
                staged.write("\n"); staged.flush(); os.fsync(staged.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
