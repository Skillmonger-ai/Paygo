# Paygo

**Give software an allowance.**

```bash
paygo exec -b 5 -- codex
```

Paygo puts a hard dollar budget around an autonomous process.

The agent can spend money to get work done.

It cannot raise its own limit.

---

## Why Paygo exists

Autonomous agents are becoming capable of doing real work, but giving software open-ended access to paid inference, search, data, APIs, and other services is uncomfortable.

Today, the usual choices are:

- give the agent API keys and trust it;
- manually approve purchases;
- build custom billing controls into every agent;
- avoid letting the agent spend money at all.

Paygo adds a simpler primitive:

```text
CAN SPEND, UP TO $X
```

Think of it like `ulimit` for money.

```bash
paygo exec --budget 10 -- my-agent
```

The agent can buy inference and machine-payable services until it reaches the budget ceiling.

Then spending stops.

---

## The idea

```text
                 EXISTING AGENT
                       │
                 wants to spend
                       │
                       ▼
                ┌─────────────┐
                │    PAYGO    │
                │             │
                │ $10 ceiling │
                └──────┬──────┘
                       │
              approved spend only
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
    inference         search          APIs
       │               │               │
       └───────────────┼───────────────┘
                       ▼
                 user-controlled
                    wallet
```

Paygo is not an agent framework.

Paygo does not plan.

Paygo does not reason.

Paygo does not custody funds.

Paygo controls one thing:

> **How much money a process is allowed to spend.**

---

## Goals

Paygo should make this boring:

```bash
paygo exec -b 5 -- codex
paygo exec -b 20 -- claude
paygo exec -b 10 -- python agent.py
paygo exec -b 2 -- node index.js
```

The process should be able to use paid services without being handed unrestricted financial credentials.

The user should always know:

```text
Authorized       $5.00
Spent            $0.83
Remaining        $4.17
```

---

## Core principles

### 1. Hard ceiling

The central invariant is:

```text
settled + reserved <= authorized
```

The agent cannot modify `authorized`.

### 2. Enforcement outside the agent

Do not trust the model or harness to honor its own budget.

Paygo runs outside the child process and authorizes spending independently.

### 3. No Paygo custody

Funds remain in a user-controlled wallet.

Paygo is a policy and payment layer, not a bank account.

### 4. No wallet private key in the agent

The child process receives narrow Paygo session credentials.

It never receives unrestricted wallet signing credentials.

### 5. One logical budget

The user thinks in:

```text
Budget: $10
```

not:

```text
$4 inference
$3 search
$3 APIs
```

Everything draws against one run ceiling.

### 6. Local-first

V0 should require:

- no Paygo cloud;
- no Paygo account;
- no dashboard;
- no hosted control plane.

The ledger lives locally.

The budget logic is open source.

---

## V0

V0 should be intentionally small.

### Required

```text
paygo init
paygo exec --budget N -- <command>
paygo status
paygo topup <run> <amount>
paygo stop <run>
paygo history
paygo inspect <run>
paygo doctor -- <command>
```

### Runtime

Paygo should provide:

- a local budget daemon;
- an atomic reservation/settlement engine;
- a local SQLite ledger;
- an x402 payment client;
- one wallet adapter;
- one inference adapter;
- an OpenAI-compatible local proxy;
- an agent-facing MCP payment tool;
- process-scoped session credentials.

### Initial stack

Keep it boring.

```text
Asset             USDC
Network           Base
Wallet            Coinbase/CDP adapter
Payments          x402
Ledger            SQLite
Inference         x402-native provider
Fallback          OpenRouter adapter
```

The exact provider should remain replaceable.

---

## `paygo exec`

The primary interface:

```bash
paygo exec --budget 5 -- codex
```

Paygo:

1. creates a run;
2. sets the authorized budget to `$5`;
3. starts the local Paygo runtime;
4. injects Paygo-compatible provider configuration;
5. launches `codex` as a child process;
6. authorizes paid requests until the budget is exhausted;
7. records every reservation and settlement;
8. revokes session credentials when the run ends.

Example:

```text
$ paygo exec -b 1 -- researcher

PAYGO
Run        pg_a81f2
Budget     $1.00

researcher → inference      -$0.03
researcher → Exa search     -$0.01
researcher → inference      -$0.07

Task complete.

Spent       $0.11
Remaining   $0.89
```

---

## Standard vs strict mode

There are two different guarantees.

### Standard

```bash
paygo exec -b 10 -- agent
```

Guarantee:

> Paygo-mediated spending cannot exceed `$10`.

If the process already possesses unrelated credentials, Paygo cannot magically revoke them.

### Strict

Target interface:

```bash
paygo exec --strict -b 10 -- agent
```

Goal:

> The launched process has no usable monetary path except through Paygo.

Strict mode should:

- scrub known provider credentials from the child environment;
- detect common pre-existing credential paths;
- warn about unenforced paths;
- eventually restrict outbound access to approved Paygo routes.

V0 may initially report strict-mode limitations rather than pretending to provide a stronger guarantee than it can enforce.

---

## `paygo doctor`

Trust requires visibility.

```bash
paygo doctor -- codex
```

Example:

```text
Codex detected

Inference routing      ✓ Paygo-compatible
x402 payments          ✓ available
Wallet key exposed     ✓ no
Existing API keys      ✓ none detected
Budget enforcement     ✓ HARD

Safe to run:

paygo exec -b 10 -- codex
```

Or:

```text
Budget enforcement     ⚠ PARTIAL

ANTHROPIC_API_KEY is currently available to this process.
The child may be able to spend outside Paygo.

Use --strict or remove the credential.
```

---

## Paid services

Paygo should prefer machine-payable services.

The clean path is:

```text
request
   │
   ▼
402 Payment Required
   │
   ▼
price / payment terms
   │
   ▼
Paygo budget check
   │
   ├── over budget → deny
   │
   ▼
authorize payment
   │
   ▼
retry request
```

Any x402-capable service can eventually become available to a Paygo-enabled agent without creating a custom billing integration.

---

## Inference

Inference is just another paid service.

Preferred architecture:

```text
USDC wallet
    │
    ├── inference via x402
    ├── search via x402
    └── data/API via x402
```

This is cleaner than maintaining separate prepaid balances.

OpenRouter is useful as a compatibility adapter because it supports broad model access and hard-capped keys, but it should not define Paygo's architecture.

---

## Security model

Assume the child agent is hostile.

The child may receive:

```text
✓ local Paygo endpoint
✓ run-scoped token
✓ approved MCP tools
```

The child must not receive:

```text
✗ wallet private key
✗ wallet administration credentials
✗ Paygo admin token
✗ provider management credentials
✗ unrestricted payment credentials
✗ ability to raise its own budget
✗ arbitrary wallet-send capability
```

---

## Threat boundary

Paygo should make precise claims.

It can guarantee:

> **Paygo will not authorize Paygo-mediated spending above the run ceiling.**

It cannot guarantee:

- a third-party payment provider has no bugs;
- a merchant cannot misbehave;
- the user did not separately give the child unrelated billing credentials;
- an already-running process can be retroactively sandboxed;
- a third-party provider always reports usage correctly.

---

## Non-goals

Do not build these in V0:

```text
Paygo cloud
web dashboard
Paygo accounts
Paygo token
custody
new blockchain
cross-chain routing
swaps
subscriptions
team expense management
marketplace
agent orchestration
planning engine
memory system
model router
service discovery
mobile app
```

Paygo should remain small.

---

## Plugin model

Paygo core should be provider-neutral.

Conceptually:

```python
class WalletAdapter:
    ...

class PaymentAdapter:
    ...

class InferenceAdapter:
    ...

class HarnessAdapter:
    ...
```

Community packages should eventually look like:

```text
paygo-wallet-coinbase
paygo-wallet-circle
paygo-provider-x402
paygo-provider-openrouter
paygo-harness-codex
paygo-harness-claude
```

The core budget engine must not depend on any one provider.

---

## Philosophy

Paygo should be easy to explain in one sentence:

> **Paygo makes it safe to let software spend money.**

And easy to try in one command:

```bash
paygo exec -b 5 -- agent
```

If the project becomes complicated enough that this stops being true, simplify it.
