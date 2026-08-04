/**
 * Command Centre — The Observe-Act Loop
 *
 * Full-height layout:
 *   1. Header with controls and status pill
 *   2. Phase strip — 13 color-coded cards with → arrows (click → /phase/N)
 *   3. Active gap panel (compact, hidden when idle)
 *   4. Forge.md editor (fills remaining space)
 */

import { useEffect, useRef, useState } from 'react';
import { RotateCcw, FileText, Play } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useStore } from '@/store';

// ── Forge.md editor ───────────────────────────────────────────────────────────

interface DocNode { node_id: string; content: string; title: string }

function ForgemdEditor() {
  const queryClient = useQueryClient();
  const { purgeDerived } = useStore();
  const { data: docNode = null } = useQuery<DocNode | null>({
    queryKey: ['forgemd-doc'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return null;
      const nodes = await r.json();
      if (!Array.isArray(nodes)) return null;
      return (nodes as Array<{ node_type: string } & DocNode>).find(n => n.node_type === 'DOCUMENT') ?? null;
    },
    refetchInterval: 30_000,
  });

  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const loadedIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (docNode && docNode.node_id !== loadedIdRef.current) {
      setDraft(docNode.content ?? '');
      loadedIdRef.current = docNode.node_id;
    }
  }, [docNode?.node_id]);

  const isDirty = !!docNode && draft !== (docNode.content ?? '');

  async function save() {
    if (!docNode || !isDirty) return;
    setSaving(true);
    await fetch(`/api/graph/nodes/${docNode.node_id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: draft, change_reason: 'user edit' }),
    }).catch(() => {});
    // Cascade: delete all derived nodes and reset pipeline phases 2-13.
    await purgeDerived();
    setSaving(false);
    loadedIdRef.current = null; // force re-sync on next query result
    queryClient.invalidateQueries({ queryKey: ['forgemd-doc'] });
  }

  return (
    <div className="h-full flex flex-col rounded-xl border border-forge-border overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 bg-forge-bg/50 border-b border-forge-border shrink-0">
        <FileText size={13} className="text-cyan-400" />
        <h2 className="text-xs font-bold font-mono text-forge-text uppercase">Forge.md</h2>
        {docNode && (
          <span className="text-[9px] font-mono text-forge-muted/50 ml-1 truncate max-w-[12rem]">
            {docNode.title || docNode.node_id}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {isDirty && <span className="text-[9px] font-mono text-amber-400">unsaved</span>}
          {isDirty && (
            <button
              onClick={save}
              disabled={saving}
              className="px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-forge-success/10 text-forge-success border border-forge-success/30 hover:bg-forge-success/20 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save & Reset'}
            </button>
          )}
        </div>
      </div>
      {docNode ? (
        <div className="flex-1 overflow-hidden">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            spellCheck={false}
            className="w-full h-full px-4 py-3 bg-black/20 font-mono text-xs text-forge-text/90 placeholder-forge-muted/40 resize-none focus:outline-none focus:bg-black/30 transition-colors"
          />
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center text-xs font-mono text-forge-muted/50 italic">
          No Forge.md loaded — run Phase 1 (Read Forge.md) to ingest it.
        </div>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function logUserAction(action: string, detail?: string) {
  const msg = detail ? `${action}: ${detail}` : action;
  useStore.getState().logUserAction(msg);
  fetch('/api/phases/log/user-action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, detail }),
  }).catch(() => { /* best-effort */ });
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export function CommandCentre() {
  const { startLoop, stopLoop, resetBuild, session } = useStore();
  const isRunning = session.loopStatus === 'RUNNING';
  const isStopping = session.loopStatus === 'STOPPING';

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">

      {/* Header & Controls */}
      <div className="flex items-center justify-between shrink-0">
        <h1 className="text-2xl font-bold font-mono text-forge-text">Command Centre</h1>
        <div className="flex items-center gap-4">
          {isRunning || isStopping ? (
            <button
              onClick={() => { stopLoop(); logUserAction('stop build'); }}
              disabled={isStopping}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-forge-error/10 text-forge-error border border-forge-error/30 hover:bg-forge-error/20 transition-all disabled:opacity-50"
              title="Stop the current build"
            >
              {isStopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              onClick={() => { startLoop(); logUserAction('run all phases'); }}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-forge-accent/10 text-forge-accent border border-forge-accent/30 hover:bg-forge-accent/20 transition-all"
              title="Run all phases end to end"
            >
              <Play size={16} /> Run All
            </button>
          )}
          <button
            onClick={() => { resetBuild(); logUserAction('reset build'); }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-forge-surface text-forge-muted border border-forge-border hover:text-forge-error hover:border-forge-error/40 transition-all"
            title="Reset graph — keeps Forge.md, deletes all derived nodes"
          >
            <RotateCcw size={16} /> Reset
          </button>
        </div>
      </div>

      {/* Phase progress is shown in the global PhaseBreadcrumb (Layout) */}

      {/* Forge.md editor */}
      <div className="flex-1 min-h-0">
        <ForgemdEditor />
      </div>
    </div>
  );
}
