# Paygo V0 — Project Plan

## 0. Mission

Build the smallest credible open-source implementation of:

> **A hard dollar budget around an autonomous process.**

Primary command:

```bash
paygo exec --budget 5 -- <command>
```

The child process may autonomously buy inference and machine-payable services.

Paygo must prevent Paygo-mediated authorized spend from exceeding the user-defined ceiling.

Do not build a general agent framework.

Do not build a wallet product.

Do not build a hosted SaaS.

---

# 1. Product contract

A successful V0 lets a user run:

```bash
paygo exec -b 5 -- python example_agent.py
```

and observe:

```text
Run          pg_123
Authorized   $5.00
Spent        $0.42
Remaining    $4.58
```

The agent may perform multiple paid operations.

All Paygo-authorized spending draws against the same run budget.

If the next operation would exceed the remaining authorization, Paygo denies it before authorizing payment.

The agent cannot top itself up.

The user can:

```bash
paygo topup pg_123 5
```

or:

```bash
paygo stop pg_123
```

---

# 2. V0 scope

## Must build

### Core

- run lifecycle;
- integer-money representation;
- atomic reserve / settle / release;
- SQLite ledger;
- local daemon;
- run-scoped session tokens;
- CLI.

### Process wrapper

- `paygo exec`;
- child-process launch;
- environment injection;
- cleanup on normal exit;
- cleanup on signal;
- `paygo doctor`.

### Paid-service path

- generic x402 HTTP buyer;
- one wallet adapter;
- Base + USDC only;
- exact-price authorization;
- idempotency;
- receipt/transaction recording.

### Inference path

- local OpenAI-compatible proxy;
- one x402-native inference adapter;
- reservation and settlement;
- one optional OpenRouter fallback adapter.

### Agent-facing tools

- MCP:
  - `paygo_request`;
  - `paygo_balance`;
  - `paygo_transactions`.

### Controls

- `paygo status`;
- `paygo topup`;
- `paygo stop`;
- `paygo history`;
- `paygo inspect`.

---

# 3. Explicit non-goals

Do not implement:

- React;
- web UI;
- authentication server;
- Paygo accounts;
- Paygo cloud storage;
- custody;
- team budgets;
- subscriptions;
- service marketplace;
- multi-chain;
- swaps;
- ERC-20 support beyond USDC;
- agent orchestration;
- generic autonomous loops;
- memory;
- prompt management;
- model selection logic;
- load balancing;
- hosted proxy;
- multiple wallet implementations;
- more than one first-class x402 inference provider before V0 passes.

If a feature is not required to demonstrate:

```text
agent can spend
+
agent cannot exceed $X
```

it is probably out of scope.

---

# 4. Architecture

```text
                      USER
                       │
          paygo exec --budget $N
                       │
                       ▼
                ┌───────────────┐
                │ PAYGO RUNTIME │
                │               │
                │ Budget Engine │
                │ Policy Engine │
                │ SQLite Ledger │
                │ Wallet Adapter│
                │ Local Proxies │
                └───────┬───────┘
                        │ launches
                        ▼
                  CHILD PROCESS
                  existing agent
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    inference        x402 tool        status
        │               │
        └───────────────┼───────────────┘
                        ▼
                 Paygo authorizer
                        │
              allowed only if within
                   run ceiling
```

---

# 5. Core invariant

Represent USD as integer microdollars.

```text
$1.00 = 1_000_000
```

Never use floats for authoritative accounting.

Run state:

```python
authorized: int
reserved: int
settled: int
```

Invariant:

```text
reserved + settled <= authorized
```

At all times.

This is the most important rule in the project.

---

# 6. Money lifecycle

Every paid operation must use the same state machine.

```text
NEW
 │
 ▼
QUOTE
 │
 ▼
RESERVE
 │
 ├── insufficient budget → REJECT
 │
 ▼
EXECUTE
 │
 ├── failure before spend → RELEASE
 │
 ▼
SETTLE
 │
 ▼
RELEASE UNUSED RESERVATION
 │
 ▼
COMPLETE
```

Never perform a payable action before a reservation exists.

---

# 7. Database

Use SQLite.

No ORM is required.

Schema:

## `runs`

```text
id TEXT PRIMARY KEY
created_at TEXT
ended_at TEXT NULL
command TEXT
authorized_microdollars INTEGER
status TEXT
```

Statuses:

```text
ACTIVE
EXHAUSTED
REVOKED
COMPLETED
FAILED
```

## `reservations`

```text
id TEXT PRIMARY KEY
run_id TEXT
provider TEXT
request_hash TEXT
reserved_microdollars INTEGER
settled_microdollars INTEGER NULL
status TEXT
created_at TEXT
settled_at TEXT NULL
```

Statuses:

```text
RESERVED
SETTLED
RELEASED
FAILED
```

## `transactions`

```text
id TEXT PRIMARY KEY
run_id TEXT
reservation_id TEXT
provider TEXT
service TEXT
kind TEXT
amount_microdollars INTEGER
currency TEXT
external_id TEXT NULL
payment_id TEXT NULL
metadata_json TEXT
created_at TEXT
```

## `sessions`

```text
id TEXT PRIMARY KEY
run_id TEXT
token_hash TEXT
created_at TEXT
revoked_at TEXT NULL
```

Do not store master secrets or wallet private keys.

---

# 8. Atomic budget operations

Implement a small `BudgetEngine`.

Required methods:

```python
create_run(...)
reserve(run_id, max_cost)
settle(reservation_id, actual_cost)
release(reservation_id)
topup(run_id, amount)
revoke(run_id)
snapshot(run_id)
```

`reserve()` must run inside an immediate SQLite transaction.

Pseudo-code:

```python
BEGIN IMMEDIATE

run = load_active_run()

remaining = (
    authorized
    - settled
    - active_reserved
)

if requested > remaining:
    ROLLBACK
    raise BudgetExceeded

create_reservation()

COMMIT
```

Concurrency correctness matters more than throughput.

---

# 9. CLI

Use a simple command surface.

```bash
paygo init

paygo exec -b 5 -- command arg1 arg2
paygo doctor -- command

paygo status [run_id]
paygo topup RUN_ID AMOUNT
paygo stop RUN_ID

paygo history
paygo inspect RUN_ID
```

Aliases:

```text
-b / --budget
```

Avoid adding flags unless they solve a V0 requirement.

---

# 10. `paygo exec`

Flow:

1. parse budget;
2. validate positive amount;
3. create run;
4. create run-scoped session token;
5. start local runtime on localhost;
6. configure child environment;
7. launch child process;
8. forward stdin/stdout/stderr;
9. handle `SIGINT`, `SIGTERM`, normal exit;
10. revoke provider sessions;
11. finalize run;
12. print spend summary.

Do not hide the child process output.

Paygo should feel like a thin wrapper.

---

# 11. Child environment

Inject only narrow runtime configuration.

Example:

```text
PAYGO_RUN_ID
PAYGO_BASE_URL
PAYGO_SESSION_TOKEN
OPENAI_BASE_URL
OPENAI_API_KEY
```

The `OPENAI_API_KEY` value is a local Paygo session token, not an upstream provider key.

Do not expose:

```text
wallet private keys
Coinbase admin credentials
OpenRouter management credentials
```

---

# 12. Local HTTP service

Bind to:

```text
127.0.0.1 only
```

Required endpoints:

```text
GET  /health
GET  /v1/paygo/balance
GET  /v1/paygo/transactions
POST /v1/paygo/request
```

Inference compatibility:

```text
POST /v1/chat/completions
POST /v1/responses
```

Anthropic compatibility can be added after the OpenAI path is stable.

Every child-facing call must validate the run-scoped session token.

---

# 13. x402 client

Implement the minimal buyer flow.

1. send HTTP request;
2. detect `402 Payment Required`;
3. parse payment requirements;
4. select USDC on Base;
5. determine maximum payable amount;
6. reserve budget;
7. call wallet adapter;
8. retry request with payment authorization;
9. record settlement;
10. return response.

Reject:

- unsupported chains;
- unsupported assets;
- invalid prices;
- missing payment requirements;
- payment amount above configured max;
- payment amount above remaining run budget.

---

# 14. Wallet interface

Core must depend on an interface, not Coinbase directly.

```python
class WalletAdapter(Protocol):
    async def address(self) -> str: ...
    async def balance(self) -> int: ...
    async def authorize_x402(self, requirements, request_id): ...
    async def revoke_session(self, run_id): ...
```

Implement exactly one production adapter for V0.

Recommended:

```text
Coinbase/CDP
Base
USDC
```

Also implement an in-memory/fake wallet for tests.

---

# 15. Inference interface

```python
class InferenceAdapter(Protocol):
    async def quote(self, request) -> Quote: ...
    async def execute(self, request, reservation) -> Response: ...
```

Preferred V0:

```text
x402-native inference
```

Fallback:

```text
OpenRouter
```

The core budget engine must not know which one is in use.

---

# 16. x402-native inference

For providers that expose a maximum price before execution:

```text
request
  │
  ▼
quote max cost
  │
  ▼
reserve max cost
  │
  ▼
authorize payment
  │
  ▼
execute inference
  │
  ▼
settle actual cost
```

This is the cleanest Paygo path because:

- no long-lived API key is needed;
- no separate provider credit balance is needed;
- inference can draw from the same USDC-backed budget as tools.

Select the first provider based on live integration reliability, not long-term strategic preference.

---

# 17. OpenRouter fallback

OpenRouter is optional compatibility infrastructure.

Implementation:

1. user supplies/stores OpenRouter management credential during `paygo init`;
2. on run creation, create an ephemeral key;
3. set a hard provider-side credit limit;
4. keep the real key inside Paygo;
5. proxy child requests;
6. reconcile spend;
7. delete/disable the key when the run ends.

Do not treat OpenRouter credits as the authoritative Paygo budget.

---

# 18. MCP

Expose a tiny MCP server.

Tools:

```text
paygo_request
paygo_balance
paygo_transactions
```

Do not expose:

```text
paygo_topup
paygo_set_budget
paygo_withdraw
paygo_send
paygo_get_secrets
```

Administrative controls are user-side CLI operations.

---

# 19. `paygo doctor`

This is a V0 trust feature, not polish.

Command:

```bash
paygo doctor -- codex
```

Inspect what can reasonably be inspected before process launch:

- known provider credentials in environment;
- Paygo wallet configuration;
- inference adapter availability;
- x402 capability;
- unsupported payment routes;
- known harness compatibility.

Example:

```text
Paygo doctor

Command                codex
Wallet                 ✓ configured
USDC / Base            ✓ available
Inference              ✓ routable
Existing OPENAI key    ✓ none
Existing Anthropic key ⚠ present
Budget guarantee       PARTIAL

The child may be able to spend outside Paygo.
```

Do not claim strict enforcement when bypass paths are known.

---

# 20. Strict mode

Do not over-engineer strict mode in the first commit.

V0 implementation:

```bash
paygo exec --strict -b 5 -- command
```

should at minimum:

- strip known cloud/inference API-key variables from the child environment;
- strip wallet-related secret variables;
- fail closed if a known unsupported bypass credential is detected;
- clearly report what Paygo cannot isolate.

Later versions may add OS/container/network-level isolation.

Never market V0 strict mode as a perfect sandbox unless it actually is one.

---

# 21. Ledger UX

`paygo status`:

```text
PAYGO — pg_123

Authorized            $5.00
Settled               $0.42
Reserved              $0.07
Available             $4.51
Status                ACTIVE
```

`paygo inspect`:

```text
18:03:14 inference     -$0.08
18:03:18 exa/search    -$0.01
18:03:24 inference     -$0.14
```

Every line should trace back to a reservation and external payment/provider identifier where available.

---

# 22. Top-ups

`topup` changes the authorization ceiling.

It does not necessarily transfer funds.

```bash
paygo topup pg_123 5
```

Atomic operation:

```text
authorized += $5
```

Only a user-side administrative path may call it.

The child run token must not authorize top-ups.

---

# 23. Stop

```bash
paygo stop pg_123
```

Must:

1. mark run `REVOKED`;
2. deny future reservations;
3. revoke ephemeral provider credentials;
4. revoke session token;
5. terminate/close local runtime as appropriate.

The wallet remains the user's.

---

# 24. Idempotency

Every paid request needs:

```text
request_id
reservation_id
payment_id
request_hash
```

A network retry must not silently become a second charge.

Tests must simulate:

- timeout before payment;
- timeout after payment;
- retry after payment;
- provider response loss;
- process crash during settlement.

---

# 25. Tests

Tests are part of the product.

## Unit

- money parsing;
- reserve;
- settle;
- release;
- topup;
- revoke;
- budget exhaustion;
- invalid state transitions.

## Concurrency

- 100 concurrent reservations;
- exact exhaustion;
- one cent remaining;
- multiple simultaneous top-ups.

## Adversarial

- child attempts top-up endpoint;
- child uses expired token;
- malformed x402 price;
- price larger than remaining budget;
- changed price on retry;
- duplicate payment;
- wallet balance lower than authorization;
- OpenRouter key escape simulation;
- daemon restart.

## Integration

- fake x402 service;
- live testnet x402 service;
- one live x402 inference provider;
- one simple agent process.

The critical property:

```python
assert total_paygo_authorized_spend <= authorized_budget
```

---

# 26. Suggested repository structure

```text
paygo/
├── README.md
├── PROJECT_PLAN.md
├── LICENSE
├── pyproject.toml
├── paygo/
│   ├── cli.py
│   ├── config.py
│   ├── runtime.py
│   ├── budget.py
│   ├── ledger.py
│   ├── sessions.py
│   ├── server.py
│   ├── x402.py
│   ├── mcp.py
│   ├── wallet/
│   │   ├── base.py
│   │   ├── fake.py
│   │   └── coinbase.py
│   ├── inference/
│   │   ├── base.py
│   │   ├── x402.py
│   │   └── openrouter.py
│   └── harness/
│       ├── generic.py
│       └── codex.py
└── tests/
    ├── test_budget.py
    ├── test_concurrency.py
    ├── test_runtime.py
    ├── test_x402.py
    └── test_adversarial.py
```

Prefer fewer files until complexity requires more.

---

# 27. Implementation stack

Start in Python for speed.

```text
Python             3.12+
CLI                Typer
HTTP server        FastAPI
HTTP client        httpx
Validation         Pydantic
Database           sqlite3
Testing            pytest
Packaging          uv
```

Avoid:

```text
Postgres
Redis
Celery
Docker requirement
Kubernetes
React
ORM complexity
```

If the interface proves valuable, a later single-binary implementation can be written in Go.

Do not delay the proof of concept for that rewrite.

---

# 28. Milestones

## Milestone 1 — budget kernel

Implement:

- SQLite schema;
- `BudgetEngine`;
- run lifecycle;
- atomic reservations;
- settlement;
- release;
- top-up;
- revoke.

No networking.

No wallets.

### Done when

```text
100 concurrent requests cannot exceed the authorized ceiling.
```

---

## Milestone 2 — process wrapper

Implement:

```bash
paygo exec -b 5 -- python examples/echo_agent.py
```

Add:

- daemon/runtime lifecycle;
- child process;
- local session token;
- status command.

### Done when

The child can query its Paygo balance but cannot administer the run.

---

## Milestone 3 — fake paid service

Create a local fake `402` service.

Implement the full:

```text
quote → reserve → authorize → retry → settle
```

flow without real money.

### Done when

An example agent can autonomously buy fake resources until its budget is exhausted.

---

## Milestone 4 — real x402

Add:

- Base;
- USDC;
- Coinbase/CDP wallet adapter;
- real x402 payment;
- transaction receipts.

Use testnet first.

### Done when

A real payable endpoint can be purchased without exposing wallet signing credentials to the agent.

---

## Milestone 5 — inference

Add one x402-native inference provider behind:

```text
POST /v1/chat/completions
POST /v1/responses
```

### Done when

A simple OpenAI-compatible agent can loop through multiple inference calls under one hard budget.

---

## Milestone 6 — MCP paid tools

Add:

```text
paygo_request
paygo_balance
paygo_transactions
```

Test with one real x402 search/data service.

### Done when

Inference and tool spend draw from the same run budget.

---

## Milestone 7 — Codex adapter

Make:

```bash
paygo exec -b 5 -- codex
```

work with minimal manual configuration.

Add `paygo doctor -- codex`.

### Done when

A fresh user can install Paygo, configure a wallet once, and launch Codex under a budget.

---

## Milestone 8 — OpenRouter fallback

Add optional OpenRouter adapter for model compatibility.

Do not make it required.

---

## Milestone 9 — hardening

Run:

- crash tests;
- retry tests;
- concurrent spend tests;
- credential exposure tests;
- malicious child tests.

Do not call V0 complete before this milestone.

---

# 29. V0 demo

The canonical demo:

```bash
paygo exec -b 1 -- python examples/research_agent.py
```

Prompt:

```text
Research a topic.
Use paid search when useful.
Continue until satisfied.
```

Expected ledger:

```text
Inference            -$0.04
Search               -$0.01
Inference            -$0.07
Contents             -$0.02
Inference            -$0.09
```

Final:

```text
Authorized       $1.00
Spent            $0.23
Remaining        $0.77
```

Then demonstrate exhaustion:

```bash
paygo exec -b 0.05 -- python examples/research_agent.py
```

Expected:

```text
Paygo budget exhausted.
No further paid operations authorized.
```

Then:

```bash
paygo topup pg_123 0.10
```

and allow the task to continue.

---

# 30. Definition of done

V0 is complete only when all are true:

- [ ] `paygo init` works
- [ ] `paygo exec -b N -- command` works
- [ ] child process receives only run-scoped Paygo credentials
- [ ] wallet secrets never reach the child
- [ ] SQLite ledger records all reservations and settlements
- [ ] x402 payment works on Base/USDC
- [ ] one real x402 inference provider works
- [ ] one real paid x402 tool works
- [ ] both consume one run budget
- [ ] budget exhaustion denies new paid actions
- [ ] child cannot raise its own budget
- [ ] user can top up
- [ ] user can stop/revoke
- [ ] `paygo doctor` reports bypass risks
- [ ] 100 concurrent spend attempts cannot exceed authorization
- [ ] retries cannot silently double charge
- [ ] provider/session credentials are revoked after run termination
- [ ] no Paygo cloud account is required
- [ ] README installation-to-first-run flow is under five minutes

---

# 31. Coding rules for Codex

When implementing this project:

1. **Prefer deletion over abstraction.**
2. **Do not add dependencies unless necessary.**
3. **Do not add a web UI.**
4. **Do not add a hosted backend.**
5. **Do not build multiple provider integrations before one works end-to-end.**
6. **Never use floating-point values for authoritative money accounting.**
7. **Never expose provider management credentials to the child process.**
8. **Every payable operation must reserve budget before execution.**
9. **Every state transition must be testable.**
10. **Every security claim in the README must correspond to an enforced property.**
11. **Fail closed when spend cannot be safely quoted or bounded.**
12. **Keep the core provider-neutral.**
13. **Keep the CLI boring.**
14. **Do not build features merely because an SDK makes them easy.**
15. **Optimize for an auditable V0, not architecture for hypothetical scale.**

---

# 32. Recommended first Codex task

Start with Milestone 1 only.

Prompt:

```text
Read README.md and PROJECT_PLAN.md in full.

Implement Milestone 1: the Paygo budget kernel.

Do not implement networking, wallets, x402, inference, MCP, or process launching yet.

Requirements:
- Python 3.12+
- SQLite
- integer microdollars
- runs, reservations, and transactions tables
- atomic reserve/settle/release/topup/revoke operations
- exhaustive unit tests
- concurrency test with 100 simultaneous reservations
- no ORM unless absolutely necessary
- no web UI
- no hosted services

The core invariant is:

    settled + active_reserved <= authorized

This invariant must hold under concurrency.

Before coding:
1. summarize the design you intend to implement;
2. list the files you will create;
3. identify the transactions/locking strategy.

Then implement it and run the full test suite.

Do not proceed to Milestone 2.
```

This deliberately forces the project to prove the only thing that matters before adding integration complexity.
