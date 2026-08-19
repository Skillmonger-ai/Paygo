# How Paygo sits on real agent harnesses

Paygo is not an agent framework. Codex, Claude Code, Pi, OpenClaw, and Hermes
already plan, tool-call, and stream. We do not replace that. We put a dollar
ceiling around the process the user already runs.

```bash
paygo exec -b 5 -- codex
paygo exec -b 5 -- claude
paygo exec -b 5 -- pi
paygo exec -b 5 -- hermes
paygo exec -b 5 -- openclaw
```

That is the whole product surface for these tools. Setup stays theirs.
Budget stays ours. `paygo doctor -- <command>` says whether leftover
credentials can still spend outside that ceiling.

---

## The rule

Giving software money should stay three facts:

```text
1. This process may spend.
2. It may not spend more than $X.
3. It cannot raise $X.
```

If attaching to a harness needs a second config language, we failed. Do not
edit `~/.codex/config.toml`, `~/.claude/settings.json`, `~/.pi/agent/`,
`~/.openclaw/openclaw.json`, or `~/.hermes/config.yaml` as the attach path.
Wrap the process. Inject env. Advertise MCP later. One buyer, one kernel.

---

## What they all share

Every one of these is a **PATH CLI** the user already knows how to install
(`curl | sh`, npm, or brew). First-run is **their** login wizard (ChatGPT /
Claude OAuth, or an API key). Home state lives in a dot-directory. They all
speak **MCP**. Several will honor a **base URL** so inference can be pointed
at a local proxy without the agent noticing.

| | Install | Binary | Home | How the user logs in |
|---|---|---|---|---|
| **Codex** | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | `codex` | `~/.codex` | ChatGPT OAuth, or `OPENAI_API_KEY` |
| **Claude Code** | `curl -fsSL https://claude.ai/install.sh \| bash` | `claude` | `~/.claude` | Claude OAuth, or `ANTHROPIC_API_KEY` |
| **Pi** | `npm i -g @earendil-works/pi-coding-agent` | `pi` | `~/.pi/agent` | `/login`, or provider env keys |
| **Hermes** | `curl … hermes-agent …/install.sh \| bash` then `hermes setup` | `hermes` | `~/.hermes` | setup wizard; keys in `~/.hermes/.env` |
| **OpenClaw** | `curl -fsSL https://openclaw.ai/install.sh \| bash` then onboard | `openclaw` | `~/.openclaw` | onboard API keys; **gateway daemon** |

OpenClaw and Hermes are **meta-harnesses**: they can spawn Codex or Claude as
a nested CLI. Wrapping `openclaw` does not automatically wrap that nested
spend. Doctor must say so.

---

## How money actually leaves each one

This is the attach map. "Unaware" means the harness does not know Paygo
exists. That is the preferred path.

### Codex

- **Inference:** ChatGPT subscription (files under `~/.codex`) *or* API key.
  Custom endpoints via `OPENAI_BASE_URL` / `openai_base_url` in user
  `config.toml`. Newer Codex prefers the Responses wire (`wire_api =
  "responses"`), not only `/v1/chat/completions`.
- **Tools:** MCP in `~/.codex/config.toml`; `codex mcp add …`.
- **Non-interactive:** `codex exec`.
- **Paygo now:** `paygo exec -b 5 -- codex`. `--strict` scrubs `OPENAI_API_KEY`.
  It does **not** revoke ChatGPT OAuth in `~/.codex`.
- **Paygo next (M5):** inject `OPENAI_BASE_URL` + a session token so API-key
  Codex is unaware. Subscription OAuth is still a bypass until the user signs
  out — doctor stays `PARTIAL`.
- **Paygo next (M6):** MCP `paygo_request` / `paygo_balance` for x402 tools.

### Claude Code

- **Inference:** Claude subscription OAuth *or* `ANTHROPIC_API_KEY`. Gateway
  mode is first-class: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` (Bearer)
  reroutes every model call, including sub-agents.
- **Tools:** `claude mcp add`; `.mcp.json` / `~/.claude.json`.
- **Paygo now:** `paygo exec -b 5 -- claude`. `--strict` scrubs Anthropic env
  keys. OAuth in `~/.claude` still spends on the Anthropic bill.
- **Paygo next:** inject `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN=<session>`
  so API-key Claude is unaware. Same honesty rule as Codex for subscriptions.

### Pi

- Small terminal harness (read / write / edit / bash). Provider list is wide.
- **Inference:** `/login` OAuth or env keys. Custom endpoints live in
  `~/.pi/agent/models.json` (`baseUrl`, `api`, `apiKey`), **not** a generic
  `OPENAI_BASE_URL` override for built-in providers.
- **Paygo now:** `paygo exec -b 5 -- pi`. Do not rewrite `models.json`.
- **Paygo next:** MCP (Pi already has it). Optional: a Paygo-provided Pi
  package later — not required to put a budget around the process.

### Hermes

- CLI *or* a messaging gateway (Telegram, Discord, …). `hermes doctor`.
- **Inference:** `~/.hermes/config.yaml` is source of truth; secrets in
  `~/.hermes/.env`. Custom OpenAI-compatible `model.base_url`. `OPENAI_BASE_URL`
  is honored only for the `openai-api` provider.
- **Tools:** MCP in `config.yaml`; can install Codex/Claude as skills and
  spawn them.
- **Paygo now:** `paygo exec -b 5 -- hermes`. Nested `codex`/`claude` may still
  use *their* logins. Doctor: `PARTIAL` if those homes exist.
- **Paygo next:** MCP + env injection for the `openai-api` / custom-endpoint
  path. Do not take over `hermes setup`.

### OpenClaw

- A **gateway** (`openclaw gateway`, Control UI on `:18789`), not only a TUI.
  Onboard writes `~/.openclaw/openclaw.json`.
- **Inference:** its own model router, *plus* plugin harnesses that execute
  through Codex (`agentRuntime.id: "codex"`) or Claude CLI
  (`agentRuntime.id: "claude-cli"`). `pi` as a runtime id is a deprecated
  alias for OpenClaw itself.
- **Paygo now:** `paygo exec -b 5 -- openclaw …` wraps that process. Nested
  Codex/Claude runtimes are separate spend paths unless they inherit Paygo
  env — do not claim HARD.
- **Paygo next:** treat OpenClaw like any other child; attach to the nested
  CLI the same way we attach to `codex` / `claude`. Never become OpenClaw's
  config language.

---

## What Paygo guarantees (and what it does not)

**Can guarantee:** Paygo-mediated spend cannot exceed the run ceiling.

**Cannot guarantee** (and must not imply):

- ChatGPT / Claude subscription OAuth in a dot-directory is billed through Paygo
- a nested CLI spawned by OpenClaw or Hermes uses the outer budget
- MCP servers the user already configured cannot spend on their own keys
- rewriting the harness config file would be "simpler" than wrapping the process

`--strict` scrubs **environment** provider keys. It is not a sandbox for
files under `~/.codex` or `~/.claude`. Doctor says `PARTIAL` when those
homes exist or when known keys are in the environment.

---

## Implementation map (keep this small)

| Front door | Who it is for | Milestone |
|---|---|---|
| `paygo exec -b N -- <cmd>` | Every harness above, today | M2 ✅ |
| `POST /v1/paygo/request` | Paygo-aware agents (demo, custom scripts) | M3 ✅ |
| OpenAI-compatible proxy + `OPENAI_BASE_URL` | Codex, Hermes `openai-api`, unaware SDKs | M5 |
| Anthropic-compatible proxy + `ANTHROPIC_BASE_URL` | Claude Code, Pi anthropic provider | after M5 |
| MCP `paygo_*` tools | Any MCP client (all five) | M6 |
| Codex-specific config rewrite | **Do not build** | — |

Harness-specific env names live in `paygo/credentials.py`. Binary identity
and doctor copy live in `paygo/harness.py`. The budget kernel never hears of
Codex.
