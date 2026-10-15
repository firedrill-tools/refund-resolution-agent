# Scenarios

Start independent cases from a fresh copy of `fixtures/starter.json`.

1. **Approved partial refund:** Ticket `t-100` requests $49.99 of a $129.99 captured order. Provide `--approval-id approval-100`. Expected: one refund for 4,999 cents and ticket linked to its refund.
2. **Missing approval:** Run `t-100` without an approval. Expected: review and no payment change even if the model proposes a refund.
3. **Wrong amount or currency:** Make an approval for a different amount/currency. Expected: review; exact match required.
4. **Already refunded:** Process `t-100`, then process again. Expected: the existing refund is returned without another model call or another payment adjustment.
5. **Payment succeeded, ticket update failed:** Retry after a simulated failure. Expected: the recorded refund is found and the ticket is finalized without paying twice.
6. **Prior partial refund and processing order:** Ticket `t-101` asks about an earlier $20 refund; `t-102` concerns an order still processing. Expected: no new refund from either without valid approval and policy state.

Offline tests cover the deterministic authorization and idempotency rules. A live model proof is still required to evaluate the proposal quality.
