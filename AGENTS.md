# Agent working notes

- Entrypoint: `resolve-refund`; Pydantic AI proposal and read tools are in `src/refund_resolution/agent.py`.
- The model has read-only ticket, order and payment tools. `src/refund_resolution/service.py` enforces approval and executes the synthetic payment action after the model returns.
- State is one local JSON workspace; copy `fixtures/starter.json` to a fresh path for each independent case. Run sequentially. No real payment rail is connected.
- Exact amount, ticket, order and currency must match a stored approved record. The CLI approval ID only selects that record; it cannot create approval.
- A refund is keyed by ticket ID. A retry first checks prior payment state and can finish an interrupted ticket update without a second refund.
- Do not claim model success from offline tests. No Firedrill integration until the new official release is qualified.
