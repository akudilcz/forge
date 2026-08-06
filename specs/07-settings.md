# 07 — Settings

Route: `/settings`. Where the user configures the LLM provider and
system-level tuning.

## Persistence

Settings are stored server-side and fetched on load via `GET /api/v1/settings`.
Edits are patched via `PATCH /api/v1/settings` (deep-merge). The form
**auto-saves 600 ms after the last keystroke**; the user does not need to
press Save. A status indicator shows `idle` / `saving` / `saved` / `error`.

## LLM provider

The user picks one provider at a time. Supported providers are Poe,
OpenRouter, Ollama, and any OpenAI-compatible API.

- **Per-provider model configs are preserved on switch.** Changing from Poe
  to OpenRouter and back restores the prior Poe model selection without the
  user re-typing it.
- **API keys** are supplied via the environment variable named by
  `api_key_env` (e.g. `POE_API_KEY`, `OPENROUTER_API_KEY`) or entered in the
  UI. A provider that requires a key but has none fails **loudly at LLM
  construction** — never as a swallowed mid-run auth error.
- **Keyless endpoints** (e.g. local Ollama) must be declared explicitly with
  `keyless: true`. Keylessness is never inferred.

## Configuration surface (`llm.*`)

Every operator-settable field, with defaults:

| Field | Default | Meaning |
|-------|---------|---------|
| `active_provider` | `poe` | Which saved provider config is active. |
| `base_url` | Poe endpoint | OpenAI-compatible API base URL. |
| `api_key_env` | `POE_API_KEY` | Environment variable holding the API key. |
| `keyless` | `false` | Explicit opt-in for endpoints needing no key. |
| `request_timeout` | `600` s | Per-request timeout. |
| `call_delay_ms` | `400` | Throttle delay between LLM calls. |
| `cache_enabled` | `true` | Global switch for the local SQLite response cache (non-streaming calls only; safety-critical call sites such as the duplicate judge opt out regardless). |
| `cache_dir` | `.cache` | Directory for `llm_cache.db`. Relative paths resolve against the repo root. |
| `trace_enabled` | `true` | Full request/response trace of every LLM call (see [11-observability.md](11-observability.md)). |
| `trace_dir` | `.forge/llm_trace` | Trace file location; relative paths resolve against the repo root. |
| `dispatch_token_budget` | `24000` | Hard cap (exact token count) on conversation history re-sent per agent dispatch; oldest messages are trimmed deterministically. |
| `mission_token_budget` | `60000` | Hard cap on the Phase 12 mission thread's history per LLM call. |
| `quality_judge_batch_size` | `25` | Max nodes judged per quality-check LLM call; larger phases are chunked so verdicts never truncate. |
| `batch_author_chunk_size` | `20` | Max items authored per batch-phase LLM call, with per-chunk retry. |
| `context_window_default` | `128000` | Assumed context window for models not listed in `model_context_windows`. |
| `agents` | per-role map | Model per agent role (Document Specialist, Requirements Engineer, Design Architect, Software Engineer, Test Engineer, Quality Auditor, Console). |
| `phase_models` | per-phase map | Model per pipeline phase (1–12); Phase 12 defaults to a stronger coding model. |
| `model_context_windows` | per-model map | Context window (tokens) per model name. |
| `providers` | Poe + OpenRouter presets | Saved per-provider configs restored on switch. |
| `options` | Ollama defaults | Inference options passed to Ollama (`temperature` 0.8, `top_p` 0.9, `repeat_penalty` 1.1, `num_thread` 8). |

Other config sections (`project`, `server`, `git`, `compliance`,
`notifications`, `tools`) follow the same read/patch mechanism; the exact
tunables surface automatically from the server-side settings schema.

## Validation

- Missing API key for a provider that requires one → loud error the first
  time an LLM is constructed; the loop halts with a clear message that
  points the user to Settings.
- Invalid base URL → save still succeeds, but the next provider health check
  fails and surfaces in the System Log.

## First-run

On first launch with no provider configured, the Command Centre Play button
is enabled but the first agent dispatch will fail with a configuration error.
The user is expected to visit Settings first.
