# Refund resolution agent with Pydantic AI and Claude

A Claude analyst reads an assigned support ticket, order and captured payment through scoped read tools, then proposes a typed decision. Application policy checks fresh identity, approval, currency and integer cents before a local synthetic payment and ticket update. The model has no approval or payment-write tool. Earlier committed payments remain inspectable after later failures.

This example repository is publicly viewable. A project license has not been selected.

## Simulate with Firedrill

[Firedrill](https://firedrill.run/) is a simulation platform for AI agents.
Start from this repository’s root:

```sh
npx firedrill-run init
```

Sign in and choose your project, then follow the [simulation run guide](firedrill/README.md)
for application dependencies, a preview and the reviewed scenarios. Your agent runs
locally or in your CI; model credentials stay separate from Firedrill sign-in.
The included recipes record outcomes without an automatic quality score or CI gate.

## Run one case

Requires Python 3.12+ and `uv`. Only a fresh model proposal requires `ANTHROPIC_API_KEY` and incurs provider usage; listing, initialization, recorded-payment recovery and offline checks need no key.

```sh
uv sync --dev --locked
uv run --locked resolve-refund --list-cases
uv run --locked resolve-refund --init-case busy --workspace run-output/busy.json
uv run --locked resolve-refund t-104 --approval-id approval-104 \
  --workspace run-output/busy.json
```

Set the key through your environment/secret manager. `--model` or `ANTHROPIC_MODEL` selects an `anthropic:<model>` identifier; the default is `anthropic:claude-sonnet-4-6`. The native endpoint is explicitly `https://api.anthropic.com`, independent of provider-global base-URL variables. `create_client` is the connection seam for a caller-supplied transport or explicit endpoint. The normal CLI has no scripted model mode. `--output-root DIR` chooses the receipt root.

`--init-case` validates bundled hashes and identities and exclusively creates a private file; it never overwrites a workspace. Its JSON prints the exact ticket/approval selectors. An approval selector only finds an existing stored authorization; it cannot create one. Use a separate state file per independent case. The four legacy `fixtures/` remain authoring/compatibility inputs; manually copying one does not establish a manifest-qualified case.

The callable seams share the normal policy/runtime:

```python
from pathlib import Path
from refund_resolution.datasets import initialize
from refund_resolution.service import resolve_async
from refund_resolution.workspace import Workspace

selected = initialize("starter", Path("run-output/starter.json"))
result = await resolve_async(selected["ticket_id"], selected["approval_id"],
    Workspace(Path(selected["workspace"])), "anthropic:claude-sonnet-4-6")
```

Synchronous applications use `resolve`; `propose_async`/`propose` expose the read-only planning seam. `build_agent` is a lower-level, caller-owned framework builder, not the complete policy/receipt runtime. Trusted injected proposers are for composition/tests and must return a validated `Proposal`; their work and observations are caller-owned.

## Native completion and resource ownership

Pinned execution uses Pydantic AI **2.56.0**, Anthropic SDK **1.14.0** and HTTPX2 **2.13.1**. A native HTTP probe found the framework accepting valid structured proposals with `max_tokens`, and tool calls marked `end_turn`. Public HTTP hooks now retain and validate a full Messages JSON response before the SDK can release a proposal or dispatch read tools. Truncated, blank, malformed, inconsistent and mixed proposal/read output fails. A model-request error never issues payment. The qualified path is nonstreaming Messages JSON; wire SSE is not qualified.

Normal limits are eight model requests, 24 read-tool calls, 4,096 output tokens, 30-second HTTP phase timeouts and a 120-second overall proposal deadline. SDK transport and framework validation retries are zero. The application owns a fresh agent, HTTP/SDK client and cancellation scope for each native proposal; success, startup failure, provider error, cancellation and deadline close them. Read tools run in the same event loop and perform bounded local reads; they do not leave background tool threads. `resolve_async` cancellation and CLI SIGINT/SIGTERM reach that scope. Cancellation cannot reverse a committed payment or establish that remote provider computation/billing stopped.

A completed receipt means policy returned `refunded`, `already_refunded`, `deny` or `review`, with current state validated. It is not a semantic correctness score. Failure/cancellation produces no successful result and CLI exits nonzero; errors expose `receipt_path`, in-memory `evidence` and available native observations. Inspection of actual effects remains necessary after an interrupted run. Forced termination may leave a `running` receipt.

## Authorization, effects and recovery

| Boundary | Supported operation |
|---|---|
| `read_ticket` | Exact assigned ticket only |
| `read_order` | That ticket's currently linked order only |
| `read_payment` | All captures for that exact order; no first-match selection |
| Application policy | Fresh approval/identity/currency/remaining balance, then one refund per ticket |
| Synthetic payment store | Adjust capture balance and append refund together in one atomic file replacement |
| Support store | Close the same ticket with its recorded refund in a later replacement |

After planning, the application rereads the file. A changed customer request/identity, closed or cancelled ticket, revoked/missing authorization, wrong amount/currency, ambiguous capture or insufficient balance requires review. Approvals must match the ticket, order, currency and exact positive integer amount. Order delivery is required. These are narrow local service subsets, not real payment/ticket API compatibility.

A retry first finds the saved refund and makes **no new model call or payment**. Recovery rechecks its payment/order/customer links and original authorized customer request before closing the ticket. A correction, cancellation or legacy refund lacking that evidence requires review. The refund preserves the original proposal, raw observations and authorization snapshot. A replay's `model_usage` is null; nested original evidence is historical and must not be counted as a new execution.

Schemas reject duplicate IDs, orphan references, invalid statuses, unsafe/coerced money and contradictory recorded refund balances. Imported ID gaps remain; new refund IDs advance from the maximum. Saves stage private files, flush/fsync, and atomically replace state. A failed replacement leaves the previous file readable; reopen it after an I/O failure. State and receipt publications are separate, and payment and ticket updates are deliberately separate commits. No rollback or remote-payment transaction protocol is claimed.

Canonical paths and an in-process claim reject overlapping resolutions, including aliases. Run one writer/process per file; there is no cross-process lock. The application restores caller observation hooks and releases its claim. Reset requires stopping the owner and initializing another file. It covers local business state, not receipts, provider retention/billing or caller-owned resources. No durable framework conversation/session is configured. No production payment rail or mailbox is connected.

## Receipts and usage

Schema-1 receipts live under `<workspace-directory>/runs/<run-id>/receipt.json` by default, with 0700 run directories and 0600 files. Atomic updates begin before model work and record initial/current/final byte hashes, declared dataset identity, actual installed SDK versions, requested/reported models, raw provider body/error text, HTTP status/request IDs, timestamps, tool-call IDs/arguments/results and committed financial effects. Inherited dataset tags do not prove an edited input remains its original snapshot: use its actual initial hash.

Raw bodies are decoded HTTP response bytes captured **before SDK defaults/validation**, not original compressed wire bytes. The 1 MiB evidence cap is checked after public HTTP buffering, so it is not a network or memory bound. An oversized response stops proposal release; original body hash, truncated content and any parseable measurements remain. A recording failure also stops further work; persistent publication failure exposes `evidence_error` in memory and cannot claim the last receipt was updated. SDK error causes remain available alongside the receipt.

`reported_usage` uses only actual nonnegative integer fields. Missing, partial, invalid and explicitly reported zero counts remain distinct. Input combines uncached, cache-creation and cache-read tokens according to [Anthropic's usage definition](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); totals are complete only when every observation reports all four necessary counts. Failed attempts are included when measured. Unknown cost stays null. `sdk_usage` separately retains normalized counters and any SDK price estimate; those defaults/estimates are not provider-reported counts or measured billing. A model-free replay has zero observed attempts and unknown new token/cost measurements.

## Data, verification and distribution

`synthetic-refund-cases` version **2** has eight hash-bound cases across starter, busy, ambiguous and boundary profiles: starter, busy, ambiguous captures, exhausted balance, missing approval, hostile note, partial recovery and already completed. Busy contains 20 coherent orders/payments, 21 tickets and seven prior human refunds, with similar customer names, USD/CAD amounts, pending approvals and ID gaps. Prior work is explicitly fictional human/local activity; no model decisions or measured usage are seeded. [Scenario briefs](scenarios/README.md) and manifest expectations stay outside target context; only the assigned business records reach its tools.

```sh
uv run --locked pytest -q
uv run --locked python scripts/build_fixtures.py
uv build
uv run --locked python scripts/check_distribution.py
```

The 62 offline checks include actual native HTTP/SDK execution, loopback CLI success/error/SIGTERM, SDK budgets/cleanup, raw usage, recording failures, corrected recovery, atomic state and dataset drift. A separate `FunctionModel` case exercises the framework loop. Controlled provider choices prove boundaries, not Claude judgment, a success rate or paid usage. CI regenerates exact dataset bytes. Wheel and source-distribution gates install fresh locked dependencies outside checkout and run help/list/eight private initializations, model-free payment recovery, failed-key evidence and real busy-state operations on macOS/Linux.

Live Claude decision quality/budget, authenticated persisted Firedrill runs, and license ownership remain parked. The included Firedrill adapter uses the existing CLI/state/receipt seams. Explore the [Firedrill examples](https://docs.firedrill.run/guides/examples), [Firedrill](https://firedrill.run/) and [official documentation](https://docs.firedrill.run/).

## Current Firedrill connection — 2026-10-19

The [agent-first integration](firedrill/README.md) pins released CLI/SDK 2.2.2 and invokes the actual application CLI with fresh private state. Caller credit amounts/approval identities remain explicit and independent of case selection. Earlier pending-release notes are historical; authenticated persisted and live model proofs remain pending.
