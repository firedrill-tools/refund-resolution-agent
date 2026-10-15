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

`ANTHROPIC_MODEL` or `--model` selects the configured Anthropic model; the default is `anthropic:claude-sonnet-4-6`. A normal invocation calls Claude through Pydantic AI `2.56.0` and may incur provider cost. The final JSON contains the resolution and SDK-reported request/token usage, requested/returned model identifiers, and unknown cost as null. All-zero unreported token counters stay null. A successful refund retains its proposal and observation in the refund record. A retry that finds that record makes no new model request and returns `model_usage: null`; its nested original observation must not be counted again.

## Records and authorization

The model can read only its assigned ticket and that ticket's current order/payment matches. It has no approval or payment-write tool. After planning, the application reloads saved state and checks the current ticket, order, payment and approval. Changed customer instructions or identity require review; a closed/cancelled ticket cannot authorize a new refund. The order, payment and approved amount must agree on currency, identity and integer cents, and the amount must fit the remaining capture.

This narrow payment adapter supports one unambiguous captured payment per order. Multiple captures, missing payment currency or inconsistent money records require review; it never chooses the first payment silently. Historical refund recovery rechecks the current ticket/order/payment relationship before closing a ticket. Legacy records lacking that evidence require review and cannot trigger a second payment.

State saves flush a staged file before atomic replacement. If replacement fails, the previous on-disk state remains readable. Discard the in-memory workspace after an I/O failure, reopen the persisted file and retry the same ticket; never assume an interrupted operation paid or failed solely from the CLI error. These safeguards preserve the one-writer restriction and do not implement a remote payment processor's transaction protocol.

## Datasets and verification

All four fixtures declare dataset `synthetic-refund-cases`, version `1`:

| Profile | File | Included situation |
|---|---|---|
| Starter | `fixtures/starter.json` | Partial refund, prior partial payment state and processing order |
| Busy ordinary | `fixtures/busy.json` | Five orders, similar customer addresses, USD/CAD payments and exact independent approvals |
| Ambiguous | `fixtures/ambiguous.json` | One order paid through two captures, requiring manual payment selection |
| Boundary | `fixtures/boundary.json` | A fully refunded CAD order and a new request with no remaining captured balance |

Copy the selected file into a fresh ignored `run-output/` path and pass `--workspace`; the target sees assigned business records, not scenario briefs. `scenarios/README.md` records case inputs, reset and completion boundaries. The CLI emits JSON after the resolution and any ticket update finish; an exception can follow a committed payment, so inspect the saved refund record before resuming.

`uv run --locked pytest -q` passes 23 offline cases. These cover four reproduced authorization bugs, fresh-record checks, exact money, atomic-save failure, restart recovery and preservation of unrelated busy-profile records. An actual Pydantic AI loop with a `FunctionModel` exercises assigned-case tools, typed output and usage serialization without calling a provider. Scripted request/token values in that check are test data, not live measurements. Claude's decision quality and real provider usage remain unverified. The Firedrill connection waits for the new official release and verified operation contract. No public license has been selected.

Explore related applications in the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog). Product and setup references: [Firedrill](https://firedrill.run/) and the [official developer documentation](https://docs.firedrill.run/). Use documentation matching the qualified release; this repository records its current integration limits above.
