# Refund resolution agent

A Pydantic AI agent using Claude inspects a support ticket, order and captured payment through read-only tools, then proposes a typed refund decision. The application applies exact approval, identity, currency and remaining-balance checks before issuing a synthetic refund and updating the ticket. The model never receives a payment-write tool.

## Run

Requires Python 3.12, `uv` and an Anthropic API key for a model run.

```sh
uv sync --dev
cp fixtures/starter.json workspace.json
ANTHROPIC_API_KEY=... uv run resolve-refund t-100 \
  --approval-id approval-100 --workspace workspace.json
uv run pytest
```

To reset, copy the fixture to a new workspace file. Run one process at a time per file; the JSON store has no cross-process transaction lock. All orders, payments and approvals are fictional. No real payment processor or customer mailbox is contacted.

`scenarios/README.md` includes the ordinary partial refund, absent/wrong approval, prior refund and interrupted ticket update. Offline tests prove deterministic policy and state behavior with an injected proposal. They do not prove Claude's decision quality. No live model run or token-usage record is available from this environment. The Firedrill connection waits for the new official release and verified operation contract. No public license has been selected.
