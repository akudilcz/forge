# 07 — Settings

Route: `/settings`. Where the user configures the LLM provider and
system-level tuning.

## Persistence

Settings are stored server-side and fetched on load via `GET /api/settings`.
Edits are patched via `PATCH /api/settings`. The form **auto-saves 600 ms
after the last keystroke**; the user does not need to press Save. A status
indicator shows `idle` / `saving` / `saved` / `error`.

## LLM provider

The user picks one provider at a time. Supported providers are Poe,
OpenRouter, Ollama, and any OpenAI-compatible API.

- **Per-provider model configs are preserved on switch.** Changing from Poe
  to OpenRouter and back restores the prior Poe model selection without the
  user re-typing it.
- **API keys** can be entered in the UI or supplied via environment variables
  (`POE_API_KEY`, `OPENROUTER_API_KEY`, etc.) before launch. UI entry takes
  precedence over environment.
- Ollama does not require a key; it requires a reachable local URL.

## Other tunables

Settings exposes:

- Model name, temperature, and max-tokens per provider.
- Base URL (for custom OpenAI-compatible endpoints).
- Optional overrides for loop cadence and concurrency.

The exact tunables available are governed by the server-side settings schema
and surface automatically when the server advertises them.

## Validation

- Missing API key for a provider that requires one → loud error on the first
  agent dispatch; the loop halts with a clear message that points the user to
  Settings.
- Invalid base URL → save still succeeds, but the next provider health check
  fails and surfaces in the System Log.

## First-run

On first launch with no provider configured, the Command Centre Play button
is enabled but the first agent dispatch will fail with a configuration error.
The user is expected to visit Settings first.
