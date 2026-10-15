"""Synthetic order, payment and ticket services backed by one local file."""
import json
from pathlib import Path


class Workspace:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text())

    def get(self, kind: str, item_id: str):
        return next((item.copy() for item in self.data[kind] if item["id"] == item_id), None)

    def payment_for(self, order_id: str):
        return next((item.copy() for item in self.data["payments"] if item["order_id"] == order_id), None)

    def approval(self, approval_id: str):
        return self.get("approvals", approval_id)

    def prior_refund(self, ticket_id: str):
        return next((r.copy() for r in self.data["refunds"] if r["ticket_id"] == ticket_id), None)

    def issue_refund(self, ticket_id: str, order_id: str, amount_cents: int, approval_id: str):
        previous = self.prior_refund(ticket_id)
        if previous:
            return previous
        payment = next(p for p in self.data["payments"] if p["order_id"] == order_id)
        if amount_cents <= 0 or amount_cents > payment["captured_cents"] - payment["refunded_cents"]:
            raise ValueError("refund exceeds remaining captured amount")
        refund = {"id": f"refund-{len(self.data['refunds']) + 1:03d}", "ticket_id": ticket_id,
                  "order_id": order_id, "amount_cents": amount_cents, "approval_id": approval_id}
        payment["refunded_cents"] += amount_cents
        self.data["refunds"].append(refund)
        self.save()
        return refund

    def close_ticket(self, ticket_id: str, refund_id: str):
        ticket = next(t for t in self.data["tickets"] if t["id"] == ticket_id)
        ticket["status"] = "refunded"
        ticket["refund_id"] = refund_id
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")
