# Paygo V0 — Implementation Plan

## Scope defense

The non-goals list lives in the README, not just the plan. Feature requests get
one question: *is it required to demonstrate "agent can spend + agent cannot
exceed $X"?* The design docs give maintainers something to point at, which is
most of what keeps small projects small.

---

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| x402 ecosystem immaturity — spec drift, few reliable merchants/providers | Fake-service CI gate (M3) keeps development unblocked; pick the first live provider empirically (M5); isolate spec knowledge in `x402.py` |
| SQLite write contention under bursty agents | Acceptable by design (correctness > throughput); WAL + busy_timeout; the write lock is held for milliseconds per reservation |
| Worst-case inference holds feel restrictive (big `max_tokens` → big hold) | Immediate release on settle; document the behavior; surface the hold size in the 402 error so agents can lower `max_tokens` themselves |
| Subscription/harness auth churn (providers change how plan auth works) | All such knowledge lives in `harness/` + the credential table — one small file to update, core untouched |
| Overclaiming safety → trust damage | AD-5 discipline: doctor verdicts, README claims backed by tests, strict mode reports limitations |
| Scope creep toward agent-framework features | Non-goals in README; the one-question test; design docs as the durable "no" |

---

## 6. Post-V0 direction (noted, not planned)

Streaming settlement; Anthropic-compatible proxy; OS/container/network-level
strict mode; additional wallet/harness packages via entry points; subscription
*quota* awareness (explicitly rejected for V0 — it is a resource limit, not
money, and drags toward model-router territory); a Go single-binary if and only
if the Python V0 proves the interface.
