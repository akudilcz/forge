# 11 — Observability

The system records every significant event of a build run — phase transitions,
agent dispatches, gap detection, LLM calls, tool invocations, graph writes,
and decisions — to a queryable structured log store, and additionally records
the **complete request and response of every LLM call** to a durable trace.
Together these answer both "what did the build do?" and "exactly what did the
model see and say?".

## Structured log store

### Live log feed

While a build is running, the Command Centre streams log events to the
`FORGE_LOG` WebSocket channel. The frontend's `/logs` viewer renders them
in near real time with:

- Colour-coded level (`INFO`, `WARN`, `ERROR`, `DEBUG`).
- Category badge (see Categories below).
- Structured columns when present: `phase`, `gap_type`, `node_id`,
  `agent_id`, `tool_name`, `duration_ms`.

### Querying historical logs

The structured log DB is exposed via `GET /api/v1/logs` with filters:

| Filter | Meaning |
|--------|---------|
| `level` | One of `INFO`, `WARN`, `ERROR`, `DEBUG`. |
| `category` | One of the canonical categories (see below). |
| `run_id` | Isolate events from a single build run. |
| `call_id` | Isolate events from a single LLM call turn. |
| `phase` | Events from a specific phase (0–14). |
| `gap_type` | Events related to a specific gap type. |
| `node_id` | Events touching a specific graph node. |
| `since_ms` / `until_ms` | Unix-ms time window. |

The response includes the flattened record plus the `extras` JSON blob for
non-promoted fields. Results are capped at 10,000 rows and ordered by
`ts_ms DESC`.

Every record carries correlation context set automatically per async task
(`run_id`, `phase`, `cycle`, `gap_type`, `node_id`, `call_id`, …), so a
single filter isolates all events of one build, one phase, one gap, or one
LLM turn.

### Retention and backpressure

At server startup, records older than **3 days** are pruned, and if the DB
still exceeds **500 MB** the oldest records are pruned until it fits. The
bounds are fixed; there is no rotation or user control. The sink is
best-effort: under backpressure records are dropped rather than blocking the
build loop, and the drop count is recorded (`logs_dropped`) and surfaced in
the summary.

## LLM call trace

Every LLM call — streaming or not, successful or failed — is appended as one
JSON record to `<llm.trace_dir>/trace.<pid>.jsonl` (default
`.forge/llm_trace/`, resolved against the repo root). Each record carries:

- The **full request** (messages and bound tool definitions) and **full
  response** (text and tool calls — assembled from chunks on the streaming
  path).
- `call_id` — the same correlation ID as the logs DB, so metadata (tokens,
  duration) in the logs joins to full bodies in the trace.
- Model, temperature, duration, token counts, the error (failures are traced
  too), and the full correlation context (run, phase, cycle, gap, node).

Records are flushed and fsynced per call — a crash loses at most the
in-flight call. Controlled by `llm.trace_enabled` (default on) and
`llm.trace_dir` (see [07-settings.md](07-settings.md)). Trace files are not
pruned automatically.

Division of labour between the three stores: the **logs DB** holds per-call
metadata, the **trace** holds full bodies with build linkage, and the
**response cache** (`llm_cache.db`) holds bodies only for cacheable
non-streaming calls, keyed by prompt with no build linkage.

## Run artifact persistence

At the end of every build run, the process's logs DB(s) and its LLM trace
are copied into `<workspace>/.forge/` next to `forge.db`, so each build's
evidence survives later pruning and can be analysed offline. A missing
source is a loud warning, never a silent skip.

## Analysis tools

Three read-only command-line reports run over the persisted artifacts:

- **`backend.scripts.phase_timing_report <logs-db>`** — where the time went:
  wall-clock span per phase, operation durations by category, and LLM hot
  spots (calls, seconds, prompt tokens) by gap type.
- **`backend.scripts.waste_report <forge-db> <logs-db> <threshold>`** —
  LLM spend that did not contribute to the finished build: repeat dispatches
  of the same gap, work on nodes later deleted (and node churn), no-op
  rewrites, and oversized prompts, with a total-vs-wasted token summary.
- **`backend.scripts.forge_watch <forge-db>`** — live progress dashboard:
  polls a running build's DB read-only and prints phase state plus node
  counts.

## Categories

Each event is tagged with exactly one `LogCategory`:

| Category | Scope |
|----------|-------|
| `LOOP` | Build-loop lifecycle events. |
| `PHASE` | Phase start/complete/no-gaps. |
| `PIPE` | Phase-pipeline step execution. |
| `BATCH` | Batch-step (competitive gap resolution). |
| `GAP` / `GAPF` | Gap dispatch and gap analysis. |
| `AGENT` | Agent dispatch/done/error. |
| `CREW` | Crew-agent thoughts, tool calls, finishes. |
| `LLM` | LLM prompt + response + content + errors. |
| `TOOL` | Tool calls + results. |
| `GRAPH` | Graph mutations (add/update/delete/reparent/edges). |
| `DECIDE` | Explicit "we chose X because Y" decisions. |
| `QUAL` / `SEMA` | Quality + semantic-dedup orchestration. |
| `RQUAL` | Requirement atomicity + EARS check. |
| `TQUAL` | Title quality: title↔content match + specificity. |
| `XQUAL` | Combined batched quality check (requirement + title axes in one call). |
| `COV` | Coverage calculations. |
| `EVAL` | Evaluate-progress tool. |
| `CONS` / `CONSIST` / `CONFORM` | Consolidation / consistency / conformance checks. |
| `DECOMP` | Incomplete-decomposition check. |
| `CTRC` | Case-trace check. |
| `SYNC` | Workspace sync file events. |
| `CGEN` / `BZEL` | Code-gen + Bazel events. |
| `AUDIT` | Phase auditor completion checks. |
| `DLVR` | Deliverables rendering. |
| `OBS` | Run-artifact persistence. |
| `QUEUE` / `POOL` / `THROT` | Work queue / agent pool / LLM throttle. |
| `HTTP` / `WS` | HTTP request lifecycle + WebSocket sessions. |
| `USER` / `AUTH` / `SYS` / `STORE` | User actions / auth / system / store events. |
| `DASH` / `FLOW` / `SCAN` / `CTX` / `AGNT` | Dashboard / flow control / scanning / context budget / agents infra. |

## Non-goals

- No rotation of the active DB file (retention is the bound).
- No export to external observability stacks.
- No structured alerting or thresholding — consumers query as needed.
