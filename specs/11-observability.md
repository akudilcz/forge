# Observability & Log Viewer

The system records every significant event of a build run — phase transitions,
agent dispatches, gap detection, LLM calls, tool invocations, graph writes,
and decisions — to a queryable structured log store. The log store is the
primary lens for diagnosing *why* a build took a given shape, and for the
frontend's live feed and post-run analysis.

## User-facing behaviour

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

### Retention

Records older than **30 days** are pruned at server startup. The
retention window is fixed; there is no user control. The sink is
best-effort: under backpressure records are dropped rather than
blocking the build loop, and the drop count is visible via
`logs_dropped` and surfaced in the summary.

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
| `QUEUE` / `POOL` / `THROT` | Work queue / agent pool / LLM throttle. |
| `HTTP` / `WS` | HTTP request lifecycle + WebSocket sessions. |
| `USER` / `AUTH` / `SYS` / `STORE` | User actions / auth / system / store events. |
| `DASH` / `FLOW` / `SCAN` / `CTX` / `AGNT` | Dashboard / flow control / scanning / context budget / agents infra. |

## Non-goals

- No rotation of the active DB file (retention is the bound).
- No export to external observability stacks.
- No structured alerting or thresholding — consumers query as needed.
