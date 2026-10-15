# Scenarios

Every fixture embeds dataset `synthetic-refund-cases`, version `1`, and its profile. Start independent cases from a fresh copy under `run-output/`. Cases 1–9 use `starter.json` unless stated; cases 10–12 identify their own profile. The target receives only assigned business records and does not see these briefs or test expectations.

1. **Approved partial refund:** Ticket `t-100` requests $49.99 of a $129.99 captured order. Provide `--approval-id approval-100`. Expected: one refund for 4,999 cents and ticket linked to its refund.
2. **Missing approval:** Run `t-100` without an approval. Expected: review and no payment change even if the model proposes a refund.
3. **Wrong amount or currency:** Make an approval for a different amount/currency. Expected: review; exact match required.
4. **Already refunded:** Process `t-100`, then process again. Expected: the existing refund is returned without another model call or another payment adjustment.
5. **Payment succeeded, ticket update failed:** Retry after a simulated failure. Expected: the recorded refund is found and the ticket is finalized without paying twice.
6. **Prior partial refund and processing order:** Ticket `t-101` asks about an earlier $20 refund; `t-102` concerns an order still processing. Expected: no new refund from either without valid approval and policy state.
7. **Correction during planning:** A sequential saved correction relinks `t-100` or changes its customer request while the model is deciding. Expected: review current state; the old proposal/approval cannot refund the previous order.
8. **Revoked approval or changed payment:** Revoke the stored approval or reduce the remaining capture before commitment. Expected: fresh checks reject the payment. These changes are cooperative local test inputs, not a claim of a concurrent-writer lock.
9. **Cancelled or relinked ticket:** A cancelled ticket cannot authorize a new payment. If a previously refunded ticket is relinked to another order, recovery requires review and must not close it with the old refund or pay again.
10. **Busy ordinary CAD refund:** Use `busy.json`, ticket `t-104`, `approval-104`, and a 2,200-cent proposal. Observe one CAD refund against `pay-104`; all other tickets and payment balances remain unchanged, including similar customer addresses.
11. **Ambiguous captures:** Use `ambiguous.json`, `t-100` and `approval-100`. Two capture records total the order value, but the narrow adapter has no approved payment selection. Expected: review without choosing either payment or changing money.
12. **Exhausted balance:** Use `boundary.json`, `t-901` and `approval-901`. The order's historical full refund leaves zero remaining capture. Expected: review and no new payment, even with a stored approval.

The offline suite also injects a labelled journal replacement failure; the previous file must remain intact. A restarted workspace retries from saved payment state, rather than mutated in-memory state. It uses the real Pydantic AI loop with a scripted model to check that other cases cannot be read and no payment tool is exposed. That verifies the framework boundary, not Claude's behavior.

The CLI returns a resolution only after its synthetic action and ticket update finish. A failed ticket update can follow a successful payment; inspect saved refunds and retry the same ticket without inventing a new identity. Stop the owning process before replacing its state file. All files are application-owned; no external service is reset or implicitly connected.

For observation-only exploration, inspect proposals, reasons and actual records without a mandatory score. To repeat a model trial or compare Anthropic model configurations, keep the dataset/input stable and use fresh workspace copies. Existing-refund replays make no new model call and are not fresh executions. Record real usage separately from missing measurements; paid campaigns require explicit scope and budget. Live proposal quality remains unverified.
