# Paygo V0 — Implementation Plan

The execution plan: what to build, in what order, how it is tested, and the
rules that keep it small. For architecture see
[`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md); for the product pitch and public
guarantees see [`README.md`](README.md).

---

## 0. Mission

Build the smallest credible open-source implementation of:

> **A hard dollar budget around an autonomous process.**

```bash
paygo exec --budget 5 -- <command>
```

The child process may autonomously buy inference and machine-payable services.
Paygo must prevent Paygo-mediated authorized spend from exceeding the
user-defined ceiling. Not an agent framework. Not a wallet product. Not a hosted
SaaS.

---

## 1. Product contract

A successful V0 lets a user run:

```bash
paygo exec -b 5 -- python examples/research_agent.py
```

and observe:

```text
Run          pg_123
Authorized   $5.00
Spent        $0.42
Remaining    $4.58
```

The agent may perform many paid operations, all drawing against one run budget.
If the next operation would exceed the remaining authorization, Paygo denies it
before authorizing payment. The agent cannot top itself up; the user can
(`paygo topup pg_123 5`) or stop the run (`paygo stop pg_123`).

---

## 2. Scope

### Must build (V0)

- **Core kernel:** run lifecycle; integer-money; atomic reserve/settle/release;
  SQLite ledger; run-scoped session tokens; CLI.
- **Process wrapper:** `paygo exec`; child launch; environment injection;
  cleanup on normal exit and on signal; `paygo doctor`.
- **Paid-service path:** generic x402 HTTP buyer; one wallet adapter; Base + USDC
  only; exact-price authorization; idempotency; receipt recording.
- **Inference path:** local OpenAI-compatible proxy; one x402-native inference
  adapter; reservation + settlement; optional OpenRouter fallback.
- **Agent-facing tools (MCP):** `paygo_request`, `paygo_balance`,
  `paygo_transactions`.
- **Controls:** `paygo status`, `topup`, `stop`, `history`, `inspect`.

### Non-goals

Web UI/React; auth server; Paygo accounts/cloud/custody; team budgets;
subscriptions; marketplace; multi-chain; swaps; non-USDC ERC-20s; agent
orchestration; memory; prompt management; model selection; load balancing;
hosted proxy; multiple wallet implementations; more than one first-class x402
inference provider before V0 passes.

---

## 3. Scope defense

The non-goals list lives in the README, not just the plan. Feature requests get
one question:

> *Is it required to demonstrate "agent can spend + agent cannot exceed $X"?*

The design docs give maintainers something to point at, which is most of what
keeps small projects small.

---

## 4. CLI surface

Keep it boring. Avoid adding flags unless they solve a V0 requirement.

```bash
paygo init
paygo exec -b 5 -- command arg1 arg2      # -b / --budget ; --strict
paygo doctor -- command
paygo status [run_id]
paygo topup RUN_ID AMOUNT
paygo stop RUN_ID
paygo history
paygo inspect RUN_ID
```

`paygo exec` flow: parse & validate budget → create run → mint run-scoped session
token → start localhost runtime → inject child env → launch child (do not hide
its output) → forward stdio → handle SIGINT/SIGTERM/normal exit → revoke
sessions/provider creds → finalize run → print spend summary.

**Child environment** receives only narrow config —
`PAYGO_RUN_ID`, `PAYGO_BASE_URL`, `PAYGO_SESSION_TOKEN`, and (for inference)
`OPENAI_BASE_URL` / `OPENAI_API_KEY` where the "key" is a local Paygo session
token, not an upstream provider key. It never receives wallet or provider
management credentials.

---

## 5. Milestones

Each milestone is done only when its check passes.

| # | Milestone | Done when | Status |
|---|---|---|---|
| 1 | Budget kernel | 100 concurrent reservations cannot exceed the ceiling | ✅ done |
| 2 | Process wrapper | Child can query its Paygo balance but cannot administer the run | ✅ done |
| 3 | Fake paid service | An example agent autonomously buys fake resources until its budget is exhausted | ✅ done |
| 4 | Real x402 | A real payable endpoint is bought without exposing wallet signing creds (testnet first) | ⬜ |
| 5 | Inference | An OpenAI-compatible agent loops through multiple inference calls under one hard budget | ⬜ |
| 6 | MCP paid tools | Inference and tool spend draw from the same run budget | ⬜ |
| 7 | Codex adapter | A fresh user installs Paygo, configures a wallet once, and launches Codex under a budget | ⬜ |
| 8 | OpenRouter fallback | Optional model-compatibility adapter (not required) | ⬜ |
| 9 | Hardening | Crash/retry/concurrency/credential-exposure/malicious-child tests pass | ⬜ |

Milestones 1–3 require **no real money** and are the CI backbone. Real money
enters at Milestone 4, testnet first.

---

## 6. Testing strategy

Tests are part of the product, and we test **start to finish** — from
`paygo init`/install all the way to funding a wallet and spending the funds.

### Layers

- **Unit:** money parsing; reserve/settle/release/topup/revoke; budget
  exhaustion; invalid state transitions.
- **Concurrency:** 100 concurrent reservations; exact exhaustion; one cent
  remaining; simultaneous top-ups.
- **Adversarial:** child attempts a top-up/admin endpoint; expired/revoked
  token; malformed x402 price; price above remaining budget; changed price on
  retry; duplicate payment; wallet balance below authorization; OpenRouter key
  escape simulation; daemon restart.
- **Integration (no real money):** a local **fake 402 service** + fake wallet run
  the full `quote → reserve → authorize → retry → settle` flow in CI.
- **Live end-to-end (opt-in, testnet-first):** install → fund → run agent → spend
  real money → hit ceiling → top up → continue. Gated behind an explicit
  environment flag so CI never spends by accident.

### The agent-framework harness suite

Start small and focused (one echo agent, one research agent), then grow a suite
of real agent frameworks and configs under `examples/` + `tests/`, each run
under `paygo exec` so wrapper/session/adapter regressions are caught
end-to-end. The invariant asserted everywhere:

```python
assert total_paygo_authorized_spend <= authorized_budget
```

### Canonical demo

```bash
paygo exec -b 1 -- python examples/research_agent.py   # spends a little
paygo exec -b 0.05 -- python examples/research_agent.py # exhausts, denies further spend
paygo topup pg_123 0.10                                 # user re-authorizes; task continues
```

---

## 7. Coding rules

1. Prefer deletion over abstraction.
2. Do not add dependencies unless necessary.
3. No web UI. 4. No hosted backend.
5. Do not build multiple provider integrations before one works end-to-end.
6. **Never use floating-point for authoritative money accounting.**
7. **Never expose provider management credentials to the child process.**
8. **Every payable operation must reserve budget before execution.**
9. Every state transition must be testable.
10. Every security claim in the README must correspond to an enforced property.
11. **Fail closed** when spend cannot be safely quoted or bounded.
12. Keep the core provider-neutral. 13. Keep the CLI boring.
14. Do not build features merely because an SDK makes them easy.
15. Optimize for an auditable V0, not architecture for hypothetical scale.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| x402 ecosystem immaturity — spec drift, few reliable merchants/providers | Fake-service CI gate (M3) keeps development unblocked; pick the first live provider empirically (M5); isolate spec knowledge in `x402.py` |
| SQLite write contention under bursty agents | Acceptable by design (correctness > throughput); WAL + busy_timeout; the write lock is held for milliseconds per reservation |
| Worst-case inference holds feel restrictive (big `max_tokens` → big hold) | Immediate release on settle; document the behavior; surface the hold size in the 402 error so agents can lower `max_tokens` themselves |
| Subscription/harness auth churn (providers change how plan auth works) | All such knowledge lives in `harness/` + the credential table — one small file to update, core untouched |
| Overclaiming safety → trust damage | Doctor verdicts and strict-mode limitation reporting; every README safety claim is backed by a test |
| Scope creep toward agent-framework features | Non-goals in README; the one-question test; design docs as the durable "no" |

---

## 9. Post-V0 direction (noted, not planned)

Streaming settlement; Anthropic-compatible proxy; OS/container/network-level
strict mode; additional wallet/harness packages via entry points; subscription
*quota* awareness (explicitly rejected for V0 — it is a resource limit, not
money, and drags toward model-router territory); a Go single-binary if and only
if the Python V0 proves the interface.

---

## 10. Definition of done (V0)

- [x] `paygo init` works
- [x] `paygo exec -b N -- command` works
- [x] child process receives only run-scoped Paygo credentials
- [x] wallet secrets never reach the child
- [x] SQLite ledger records all reservations and settlements
- [ ] x402 payment works on Base/USDC
- [ ] one real x402 inference provider works
- [ ] one real paid x402 tool works
- [ ] both consume one run budget
- [x] budget exhaustion denies new paid actions
- [x] child cannot raise its own budget
- [x] user can top up
- [x] user can stop/revoke
- [x] `paygo doctor` reports bypass risks
- [x] 100 concurrent spend attempts cannot exceed authorization
- [x] retries cannot silently double charge
- [x] provider/session credentials are revoked after run termination
- [x] no Paygo cloud account is required
- [ ] README installation-to-first-run flow is under five minutes
