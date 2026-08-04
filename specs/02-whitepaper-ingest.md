# 02 — Whitepaper Ingest

FORGE reads the user's specification from a single file on disk and brings it
into the Project Graph as a DOCUMENT node. This is the entry point for every
downstream agent.

## Trigger

Phase 1 runs automatically as the first step after the user presses **Play**.
It is a deterministic file read; no agent, no LLM.

## Inputs

- A file named **`forge.md`** in the workspace root.

## Effects

- A **DOCUMENT** node is created as a child of PROJECT.
- The entire file contents are stored in the DOCUMENT node's `content` field.
- A `properties.slug` is set on the DOCUMENT node for lookup.

## Errors

- If `forge.md` does not exist at the expected path, Phase 1 fails loudly with
  a clear error and the loop halts. The user is expected to place the file and
  re-press Play.
- The file is read verbatim; no silent encoding conversion or truncation.

## Updating the whitepaper

Editing `forge.md` does not automatically re-ingest. The user re-triggers
ingest by re-running Phase 1 (either via the phase dashboard action or by
restarting the loop). Downstream nodes that trace to the DOCUMENT are marked
stale by the Gap Analyser when the content hash changes.

## Multiple documents

The graph permits more than one DOCUMENT under PROJECT (one per source
specification file), but the standard flow creates exactly one from `forge.md`.
Support for multi-document projects is available at the graph layer; the UI
flow for submitting additional documents is not a first-class user feature in
the current release.
