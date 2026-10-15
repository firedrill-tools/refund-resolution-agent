# Agent working notes

- Entrypoint: `resolve-refund`; Pydantic AI proposal and read tools are in `src/refund_resolution/agent.py`.
- The model has read-only ticket, order and payment tools. `src/refund_resolution/service.py` enforces approval and executes the synthetic payment action after the model returns.
- State is one local JSON workspace; copy `fixtures/starter.json` to a fresh path for each independent case. Run sequentially. No real payment rail is connected.
- Exact amount, ticket, order and currency must match a stored approved record. The CLI approval ID only selects that record; it cannot create approval.
- A refund is keyed by ticket ID. A retry first checks prior payment state and can finish an interrupted ticket update without a second refund.
- Reload saved records after model planning. A changed ticket, closed request, revoked approval, mismatched/unknown payment currency, ambiguous payment matches or invalid integer amounts must stop a new refund. One captured payment per order is supported; do not silently choose among multiple captures.
- Read tools are scoped to the assigned ticket and its current order. Keep approval and payment-write operations outside the Pydantic AI tool set. `build_agent` permits a framework test model, while the normal `propose` path still requires an Anthropic key and configured Anthropic model.
- `fixtures/starter.json`, `busy.json`, `ambiguous.json` and `boundary.json` embed dataset ID/version/profile. Each independent case gets its own copy. Saves stage and flush before atomic replacement; reopen the file after an I/O failure. Run one writer per file; no concurrent-writer guarantee is provided.
- A refund record retains its exact payment ID, currency and original proposal observation. Recovery must not close a ticket that was relinked to another order or conflicts with the recorded payment. A replay makes no model call and returns no new usage; preserve the original observation without counting it twice. Unknown token measurements/cost stay null.
- Verification: `uv sync --dev --locked && uv run --locked pytest -q` runs 23 offline checks, including an actual Pydantic AI `FunctionModel` tool loop. No scripted usage value is a live provider result.
- Do not claim model success from offline tests. No Firedrill integration until the new official release is qualified.
