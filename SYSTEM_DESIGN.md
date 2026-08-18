# Paygo — System Design

> **Paygo makes it safe to let software spend money.**
>
> It puts a hard dollar budget around an autonomous process: the process can buy
> inference and machine-payable services, but it cannot raise its own limit.

```bash
paygo exec -b 5 -- codex
```

This document describes *how* Paygo is built. For the product pitch and public
guarantees, see [`README.md`](README.md). For the milestone roadmap, scope
rules, and test plan, see [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

---

## 1. What Paygo is (and is not)

Paygo is a **policy and payment layer**. It is the thing that sits between an
existing agent and the money.

```text
                 EXISTING AGENT
                       │  wants to spend
                       ▼
                ┌─────────────┐
                │    PAYGO    │  authorize only within the run ceiling
                │ $10 ceiling │
                └──────┬──────┘
                       ▼
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
    inference        search           APIs
       └───────────────┼───────────────┘
                       ▼
                 user-controlled wallet
```

Paygo is **not** an agent framework. It does not plan, reason, custody funds, or
route models. It controls exactly one thing: *how much money a process is
allowed to spend.*

---

## 2. Design principles

These are load-bearing. Every component below is a consequence of one of them.

1. **Hard ceiling.** The central invariant is `settled + reserved <= authorized`.
   The agent cannot modify `authorized`.
2. **Enforcement outside the agent.** Do not trust the model or harness to honor
   its own budget. Paygo runs *outside* the child process and authorizes spend
   independently.
3. **No Paygo custody.** Funds stay in a user-controlled wallet. Paygo is not a
   bank account.
4. **No wallet key in the agent.** The child receives narrow, run-scoped Paygo
   credentials — never unrestricted wallet signing material.
5. **One logical budget.** The user thinks in `Budget: $10`, not per-category
   sub-budgets. Everything draws against one run ceiling.
6. **Local-first.** V0 requires no cloud, account, dashboard, or hosted control
   plane. The ledger lives on disk; the budget logic is open source.
7. **Fail closed.** When spend cannot be safely quoted or bounded, deny it.
8. **Provider-neutral core.** The budget engine must not depend on any one
   wallet, payment rail, or inference provider.

---

## 3. Component map

```text
                      USER
                       │  paygo exec --budget $N -- <command>
                       ▼
   ┌───────────────────────────────────────────────────────┐
   │                     PAYGO RUNTIME                       │
   │                                                         │
   │   CLI (Typer)          run lifecycle + admin commands   │
   │   BudgetEngine ◄──────  the kernel: reserve/settle/…    │
   │   SQLite ledger         runs, reservations, transactions│
   │   SessionManager        run-scoped token mint/verify    │
   │   Local HTTP service    127.0.0.1 only, token-gated     │
   │   x402 client           minimal 402 buyer flow          │
   │   Wallet adapter        USDC on Base (Coinbase/CDP)     │
   │   Inference adapter     x402-native / OpenRouter        │
   │   MCP server            paygo_request/balance/transactions│
   └───────────────────────────┬─────────────────────────────┘
                               │ launches (child env injected)
                               ▼
                         CHILD PROCESS
                         existing agent
                               │
                 inference · x402 tool · status
                               ▼
                       Paygo authorizer
                     (allowed only within ceiling)
```

| Module | Responsibility | Milestone |
|---|---|---|
| `paygo/money.py` | Integer-microdollar money; parse/format | M1 ✅ |
| `paygo/errors.py` | Typed domain errors | M1 ✅ |
| `paygo/ledger.py` | SQLite schema + connection factory + migrations | M1 ✅ |
| `paygo/budget.py` | `BudgetEngine` — the atomic kernel | M1 ✅ |
| `paygo/config.py` | Local-first paths + `config.toml` (no secrets) | M1 ✅ |
| `paygo/cli.py` | Typer command surface | M1 ✅ / M2 |
| `paygo/credentials.py` | Known provider/wallet env-var taxonomy (one file) | M2/M3 ✅ |
| `paygo/sessions.py` | Run-scoped session token mint/verify/revoke | M2 ✅ |
| `paygo/service.py` | Localhost HTTP service (token-gated) | M2 ✅ / M3 |
| `paygo/runtime.py` | Run supervisor + child-environment builder | M2 ✅ |
| `paygo/x402.py` | Minimal 402 buyer flow (isolated spec knowledge) | M3 ✅ |
| `paygo/wallet.py` | `WalletAdapter` + fake + routing wallet | M3 ✅ |
| `paygo/demo.py` | Built-in fake 402 merchant (separate origin) | M3 ✅ |
| `paygo/coinbase.py` | Coinbase/CDP adapter (optional extra; provisioning) | M4 🚧 |
| `paygo/inference/` | `InferenceAdapter` protocol + x402 / OpenRouter | M5/M8 |
| `paygo/mcp.py` | Agent-facing MCP tools | M6 |
| `paygo/harness/` | Harness adapters (generic, codex) | M7 |

---

## 3a. Composition surfaces

Paygo is not an agent framework. It has to sit *on top of* agent frameworks
without knowing how they plan, tool-call, or stream. That constraint is what
makes the architecture composable: every framework is just a child process that
wants to spend, and Paygo offers a small number of **front doors** onto one
enforcement path.

```text
     Codex / Claude / LangChain / a Python script / a Node agent
           │                │                │
           │  (1) unaware   │  (2) aware     │  (3) tool-calling
           │  OpenAI SDK    │  HTTP client   │  MCP
           ▼                ▼                ▼
     /v1/chat/completions   /v1/paygo/*      paygo_* tools      ← front doors
     (M5)                   (M2/M3)          (M6)
           │                │                │
           └────────────────┴────────────────┘
                            ▼
                     X402Buyer.buy()         ← the only spend path
                            │
              reserve → wallet.authorize → retry → settle
                            ▼
                      BudgetEngine           ← the only ceiling
```

Four front doors, **one** buyer, **one** kernel. Adding a framework is adding a
front door (or using an existing one), never a second budget.

| Surface | Who uses it | Cooperation required | When |
|---|---|---|---|
| Process wrapper (`paygo exec -- cmd`) | Any executable | None. Paygo injects env and wraps the process. | M2 ✅ |
| HTTP spend API (`POST /v1/paygo/request`) | Paygo-aware agents | Read three env vars, make HTTP calls. | M3 |
| OpenAI-compatible proxy (`/v1/chat/completions`) | Unaware SDKs (Codex, OpenAI clients) | None beyond `OPENAI_BASE_URL` / `OPENAI_API_KEY`. | M5 |
| MCP tools (`paygo_request`, `paygo_balance`, `paygo_transactions`) | Tool-calling agents | Advertise the MCP server. | M6 |

The child-environment builder (`paygo.runtime.build_child_environment`) is the
extension point for harness adapters. M5/M7 add `OPENAI_BASE_URL` /
`OPENAI_API_KEY` (the "key" is the Paygo session token) there — not by forking
the process-launch logic in the CLI.

### What the child sees

Three variables, always:

```text
PAYGO_RUN_ID
PAYGO_BASE_URL          127.0.0.1, token-gated
PAYGO_SESSION_TOKEN     run-scoped; hash stored, plaintext never
```

Plus, while the only payable origin is the built-in demo merchant (M3):

```text
PAYGO_DEMO_MERCHANT_URL     a *separate* origin that speaks 402
```

The merchant is not mounted on the Paygo service on purpose. Paygo is the
**buyer**; the merchant is a resource that demands payment. M4 swaps in a real
merchant URL and the Coinbase wallet for Base quotes; the child-facing
`paygo/request` contract does not change. The demo merchant stays payable via
`RoutingWallet` so Coinbase opt-in never breaks the local spend path.

---

## 3b. The paid-request primitive

`POST /v1/paygo/request` is the only way a child spends. Body:

```text
{ "url": "...", "method": "GET"|"POST", "json": {...}?, "request_id": "..."? }
```

The token binds the run — the child cannot pick a different `run_id`. The
server then runs the money lifecycle (section 5) against that URL:

```text
probe merchant
  │
  ├── 200, no 402 → return body (free resource; no reservation)
  │
  ▼
402 PAYMENT-REQUIRED
  │
  ▼
parse accepts[]  →  select exact USDC/USD  →  reserve max
  │                                              │
  │                                              ▼
  │                                    wallet.authorize_x402
  │                                              │
  ▼                                              ▼
retry with PAYMENT-SIGNATURE  ──failure──▶  release, deny
  │
  ▼
200 + body  →  settle actual  →  return envelope to child
```

x402 wire knowledge (header names, base64 JSON, `accepts[]` selection) lives
**only** in `paygo/x402.py`. Wallet signing lives **only** behind
`WalletAdapter`. The kernel never hears of 402.

A hostile child that calls the merchant directly cannot produce a valid
`PAYMENT-SIGNATURE`: the demo wallet's MAC key never leaves the Paygo process.
A hostile child that calls `paygo/request` still cannot exceed the ceiling,
because `reserve()` runs first.

---

## 3c. Consumer setup

Setup is a product surface. Two paths, one ledger, **no secrets on disk**.

```text
uv tool install git+https://github.com/Skillmonger-ai/Paygo   # once; puts paygo on PATH
paygo init
paygo demo
```

`uv` / `pipx` is the *installer*, the same role `npm i -g` plays for Codex.
After that, `paygo` is a normal command. `uv run` is not part of the product.

`paygo init` is idempotent. First run defaults to demo. `--wallet coinbase` is
a one-time opt-in. Re-running without flags **keeps** the current wallet so a
bare `paygo init` cannot clobber Coinbase setup back to demo.

```text
~/.paygo/
  ledger.db          runs, reservations, sessions
  config.toml        wallet.kind / network / address   ← never secrets
```

Coinbase credentials (`CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`,
`CDP_WALLET_SECRET`) live only in the environment. They are always stripped
from the child, even without `--strict`. The optional extra
(`uv tool install --force 'paygo[coinbase]'`) is required only
to provision an address, faucet testnet USDC, and sign Base quotes.

`paygo doctor` is the setup checklist: ledger present? which wallet? paid path
ready? provider-key bypass? Honest `HARD` / `PARTIAL` verdict.

---

## 4. The budget invariant

Represent USD as **integer microdollars**. Never use floats for authoritative
accounting — binary floats cannot represent most decimal cents, and error
compounds under repeated addition.

```text
$1.00 == 1_000_000 microdollars
```

Per-run state is three integers:

```text
authorized   the ceiling the user set (only user-side ops can change it)
reserved     sum of currently-held (not yet settled) reservations
settled      sum of finalized charges
```

The invariant, which must hold **at all times, under concurrency**:

```text
settled + active_reserved <= authorized
```

This is the most important rule in the project. Everything else is plumbing
around keeping it true.

---

## 5. Money lifecycle

Every paid operation moves through the same state machine. A payable action is
**never** performed before a reservation exists.

```text
NEW → QUOTE → RESERVE ──insufficient budget──▶ REJECT
                 │
                 ▼
              EXECUTE ──failure before spend──▶ RELEASE
                 │
                 ▼
               SETTLE (actual cost) → RELEASE UNUSED → COMPLETE
```

- **RESERVE** takes the *maximum* possible cost (the quote) so the ceiling can
  never be blown by a surprise.
- **SETTLE** records the *actual* cost; the difference is released implicitly the
  moment the reservation flips `RESERVED → SETTLED` (it no longer counts toward
  `active_reserved`).
- Settling above the reserved max is refused (fail closed): a merchant reporting
  a higher price than it quoted must not silently overdraw the budget.

---

## 6. Data model

SQLite, no ORM. The ledger is the single source of truth. Four tables:

```text
runs           id, created_at, ended_at, command,
               authorized_microdollars, status
               status ∈ {ACTIVE, EXHAUSTED, REVOKED, COMPLETED, FAILED}

reservations   id, run_id, provider, request_hash,
               reserved_microdollars, settled_microdollars,
               status, created_at, settled_at
               status ∈ {RESERVED, SETTLED, RELEASED, FAILED}

transactions   id, run_id, reservation_id, provider, service, kind,
               amount_microdollars, currency, external_id, payment_id,
               metadata_json, created_at

sessions       id, run_id, token_hash, created_at, revoked_at
```

The ledger never stores master secrets or wallet private keys. Sessions store a
**hash** of the token, never the token itself.

---

## 7. Concurrency model

Correctness beats throughput. Bursty agents issuing many simultaneous
reservations must never collectively exceed the ceiling.

- **WAL journal mode** so readers never block the single writer.
- **`busy_timeout`** so a competing writer waits for the lock instead of failing
  with "database is locked".
- **`BEGIN IMMEDIATE`** wraps the whole read-modify-write of `reserve()`, taking
  the write lock up front. This serializes reservers: two of them cannot both
  observe the same `remaining` and both succeed.
- The write lock is held only for the milliseconds of a single reservation, so
  serialization is cheap in practice.

The proof obligation (a test, not a hope): *100 concurrent reservations cannot
exceed the authorized ceiling* — and it must be tight (exactly the ceiling gets
reserved, no more, no less).

---

## 8. Security & threat model

**Assume the child agent is hostile.**

The child *may* receive:

```text
✓ local Paygo endpoint (127.0.0.1)
✓ run-scoped session token
✓ approved MCP tools
```

The child must **never** receive:

```text
✗ wallet private key            ✗ Paygo admin token
✗ wallet admin credentials      ✗ provider management credentials
✗ unrestricted payment creds    ✗ the ability to raise its own budget
```

### What Paygo guarantees

> Paygo will not authorize Paygo-mediated spending above the run ceiling.

### What Paygo cannot guarantee

- that a third-party payment provider or merchant has no bugs or misbehavior;
- that the user did not separately hand the child unrelated billing credentials;
- that an already-running process can be retroactively sandboxed;
- that a provider always reports usage correctly.

### Standard vs strict mode

- **Standard:** Paygo-mediated spending cannot exceed the ceiling. Pre-existing
  unrelated credentials are out of Paygo's reach.
- **Strict** (`--strict`): scrub known provider/wallet credentials from the child
  environment, detect common bypass paths, and *report* what cannot be isolated.
  V0 must never market strict mode as a perfect sandbox unless it actually is
  one.

### `paygo doctor` — trust through visibility

Before launch (and after `paygo init` with no arguments), inspect what can be
inspected: ledger present, wallet kind, whether Coinbase setup is complete,
known provider credentials in the environment. Print an honest verdict:
`HARD` or `PARTIAL`. Never claim strict enforcement when a bypass path is known.

---

## 9. Idempotency

A network retry must never become a second charge. Every paid request carries a
`request_id`, `reservation_id`, `payment_id`, and `request_hash`. Settlement is a
one-way state transition, so a replayed settle is rejected rather than
double-counted. The kernel is tested against: timeout before payment, timeout
after payment, retry after payment, provider response loss, and crash during
settlement.

---

## 10. Adapter / plugin model

The core is provider-neutral. Concrete providers implement small protocols:

```python
class WalletAdapter(Protocol):
    async def address(self) -> str: ...
    async def balance(self) -> int: ...
    async def authorize_x402(self, requirements, request_id): ...
    async def revoke_session(self, run_id): ...

class PaymentAdapter(Protocol): ...      # x402 buyer flow
class InferenceAdapter(Protocol):        # quote() then execute()
    async def quote(self, request) -> Quote: ...
    async def execute(self, request, reservation) -> Response: ...

class HarnessAdapter(Protocol): ...      # codex, claude, generic
```

V0 ships exactly one production wallet (Coinbase/CDP, USDC on Base) plus an
HMAC fake for tests and the M3 demo merchant, and one first-class x402
inference provider (chosen empirically for reliability). Spec knowledge is
isolated in `x402.py` so churn touches one file, not the core. The protocol and
fake live in `paygo/wallet.py`; the Coinbase adapter lives in
`paygo/coinbase.py`. `RoutingWallet` is how both stay mounted at once.

---

## 11. Testing architecture

Tests are part of the product (see [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for the full plan). Layers, from cheapest to most end-to-end:

- **Unit** — money parsing, reserve/settle/release/topup/revoke, invalid
  transitions.
- **Concurrency** — 100 simultaneous reservations, exact exhaustion, one cent
  remaining, simultaneous top-ups.
- **Adversarial** — child attempts admin ops, expired/revoked token, malformed or
  changed x402 price, duplicate payment, daemon restart.
- **Integration** — a local **fake 402 service** and fake wallet let the full
  `quote → reserve → authorize → retry → settle` flow run in CI with no real
  money.
- **Live end-to-end** — the real proof: *install Paygo → fund a wallet → run an
  agent under a budget → watch it spend real money → hit the ceiling → top up →
  continue.* This is gated behind explicit opt-in and testnet-first.

The critical property asserted everywhere:

```python
assert total_paygo_authorized_spend <= authorized_budget
```

A growing **harness suite** (`examples/` + `tests/`) exercises real agent
frameworks and configs against Paygo so regressions in the wrapper, session, or
adapter layers are caught end-to-end.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| x402 ecosystem immaturity — spec drift, few reliable merchants/providers | Fake-service CI gate (M3) keeps development unblocked; pick the first live provider empirically (M5); isolate spec knowledge in `x402.py` |
| SQLite write contention under bursty agents | Acceptable by design (correctness > throughput); WAL + busy_timeout; the write lock is held for milliseconds per reservation |
| Worst-case inference holds feel restrictive (big `max_tokens` → big hold) | Immediate release on settle; document the behavior; surface the hold size in the 402 error so agents can lower `max_tokens` themselves |
| Subscription/harness auth churn (providers change how plan auth works) | All such knowledge lives in `harness/` + the credential table — one small file to update, core untouched |
| Overclaiming safety → trust damage | Doctor verdicts and strict-mode limitation reporting; every README safety claim is backed by a test |
| Scope creep toward agent-framework features | Non-goals in README; the one-question test; design docs as the durable "no" |

---

## 13. Non-goals (V0)

Paygo cloud · web dashboard · Paygo accounts · Paygo token · custody · new
blockchain · cross-chain routing · swaps · subscriptions · team expense
management · marketplace · agent orchestration · planning engine · memory
system · model router · service discovery · mobile app.

If a feature is not required to demonstrate *"agent can spend + agent cannot
exceed $X,"* it is probably out of scope.

---

## 14. Post-V0 direction (noted, not planned)

Streaming settlement; Anthropic-compatible proxy; OS/container/network-level
strict mode; additional wallet/harness packages via entry points; subscription
*quota* awareness (explicitly rejected for V0 — it is a resource limit, not
money, and drags toward model-router territory); a Go single-binary if and only
if the Python V0 proves the interface.

---

## 15. Implementation stack

```text
Python 3.12+ · Typer (CLI) · FastAPI (local HTTP) · httpx (client) ·
Pydantic (validation) · sqlite3 (ledger) · pytest (tests) · uv (packaging)
```

Avoided: Postgres, Redis, Celery, a Docker requirement, Kubernetes, React, ORM
complexity.
