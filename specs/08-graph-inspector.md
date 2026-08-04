# 08 — Graph Inspector & Agent Inspector

Two inspector surfaces let the user explore what FORGE has built and how.

## Graph Inspector — `/graph-inspector`

The user's window into the Project Graph.

### What it shows

- **Tree graph view** — the full project graph rendered as a tree rooted at
  PROJECT. Nodes are colour-coded by type (PROJECT, DOCUMENT, PARA, HLR, LLR,
  DESIGN, TEST_CASE, RESULT, etc.).
- **Node table panel** — a flat filterable list of nodes, with type, title,
  layer, and content status.
- **Node context panel** — selected node's full content, properties,
  `trace_to` and `trace_from` edges, and version history.
- **Breadcrumb** — current phase context for orientation.

### What the user can do

- Navigate up/down the graph by clicking edges.
- Filter the node table by type, status, or free text.
- Follow traceability chains in both directions: from a paragraph to every
  downstream artefact; from a test result back to the paragraph that required
  it.
- Read (not edit) content. The graph is agent-written; direct user edits are
  not a first-class feature.

## Agent Inspector — `/agent-inspector`

The user's window into **what agents have done**.

### What it shows

- Per-agent transcript: every tool call, arguments, and return value, in
  order.
- Filter by agent, by phase, by gap, or by time range.
- Link from any tool call to the node it read or wrote.

### Source of truth

The inspector is a read-only view over the Project Graph
(`.forge/forge.db`), the pretty-print log tail (`.forge/forge.log`), and
the structured observability store (`.forge/forge.logs.db`). A completed
run is fully replayable after the fact; nothing is ephemeral.
