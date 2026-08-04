/**
 * NodeContextPanel — INNER/MIDDLE/OUTER context bundle viewer.
 *
 * Fetches /api/graph/nodes/{id}/context and renders three collapsible
 * sections showing what an agent would see when regenerating this node.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, RefreshCw, Layers, GitBranch } from 'lucide-react';
import type { GNode } from '@/components/NodeTablePanel';
import { TYPE_COLOR, nodeTypeClass } from '@/lib/nodeColors';

// ── Types ─────────────────────────────────────────────────────────────────────

interface InnerEntry {
  role: 'parent' | 'child' | 'contract';
  node_id: string;
  node_type: string;
  title: string;
  content: string;
  layer: number;
}

interface MiddleEntry {
  role: 'sibling';
  node_id: string;
  node_type: string;
  title: string;
  summary: string;
}

interface OuterEntry {
  role: 'ancestor';
  node_id: string;
  node_type: string;
  title: string;
}

interface ContextBundle {
  node_id: string;
  inner: InnerEntry[];
  middle: MiddleEntry[];
  outer: OuterEntry[];
}

// ── Sub-components ────────────────────────────────────────────────────────────

const ROLE_ICON: Record<string, string> = {
  parent: '↑',
  child: '●',
  contract: '▲',
  sibling: '·',
  ancestor: '⬆',
};

const ROLE_COLOUR: Record<string, string> = {
  parent: 'text-sky-400',
  child: 'text-emerald-400',
  contract: 'text-amber-400',
  sibling: 'text-slate-400',
  ancestor: 'text-purple-400',
};

function TierHeader({
  label,
  count,
  open,
  onToggle,
}: {
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-2 py-1.5 text-left hover:bg-forge-bg/30 transition-colors rounded"
    >
      {open ? (
        <ChevronDown size={12} className="text-forge-muted shrink-0" />
      ) : (
        <ChevronRight size={12} className="text-forge-muted shrink-0" />
      )}
      <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-forge-muted">
        {label}
      </span>
      <span className="ml-auto text-[10px] font-mono text-forge-muted/60">{count}</span>
    </button>
  );
}

function InnerSection({ entries }: { entries: InnerEntry[] }) {
  return (
    <ul className="space-y-2 mt-1">
      {entries.map((e) => (
        <li key={`${e.role}-${e.node_id}`} className="flex gap-2 text-xs">
          <span className={`shrink-0 w-4 font-mono ${ROLE_COLOUR[e.role] ?? ''}`}>
            {ROLE_ICON[e.role] ?? '·'}
          </span>
          <div className="min-w-0">
            <div className="flex items-baseline gap-1.5 flex-wrap">
              <span className={`font-mono text-[10px] px-1 rounded border ${TYPE_COLOR[e.node_type] ?? 'text-slate-400 bg-slate-400/10 border-slate-400/20'}`}>
                {e.node_type}
              </span>
              <span className="font-mono text-forge-text truncate">{e.title}</span>
            </div>
            {e.content && (
              <p className="text-forge-muted/70 mt-0.5 leading-relaxed line-clamp-2">
                {e.content.slice(0, 160)}{e.content.length > 160 ? '…' : ''}
              </p>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function MiddleSection({ entries }: { entries: MiddleEntry[] }) {
  return (
    <ul className="space-y-1 mt-1">
      {entries.map((e) => (
        <li key={e.node_id} className="flex gap-2 text-xs">
          <span className="shrink-0 w-4 font-mono text-slate-500">·</span>
          <p className="font-mono text-forge-muted/80 leading-relaxed break-all">
            {e.summary.length > 180 ? e.summary.slice(0, 180) + '…' : e.summary}
          </p>
        </li>
      ))}
    </ul>
  );
}

function OuterSection({ entries }: { entries: OuterEntry[] }) {
  return (
    <ul className="space-y-1 mt-1">
      {entries.map((e) => (
        <li key={e.node_id} className="flex items-baseline gap-2 text-xs">
          <span className={`shrink-0 w-4 font-mono ${ROLE_COLOUR.ancestor}`}>⬆</span>
          <span className={`font-mono text-[10px] px-1 rounded border shrink-0 ${TYPE_COLOR[e.node_type] ?? 'text-slate-400 bg-slate-400/10 border-slate-400/20'}`}>
            {e.node_type}
          </span>
          <span className="font-mono text-forge-muted/80 truncate">{e.title}</span>
        </li>
      ))}
    </ul>
  );
}

// ── Trace Links Section ───────────────────────────────────────────────────────

function TraceLinksSection({ node }: { node: GNode }) {
  const [open, setOpen] = useState(true);

  // Reuse the already-cached graph-nodes list to resolve labels and find reverse links.
  const { data: allNodes = [] } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: () => fetch('/api/graph/nodes').then(r => r.json()),
    staleTime: 30_000,
  });

  const outgoing = (node.trace_to as string[] | undefined) ?? [];

  // Nodes whose trace_to list includes this node (reverse / incoming links).
  const incoming = allNodes.filter(n => {
    const links = (n.trace_to as string[] | undefined) ?? [];
    return links.includes(node.node_id);
  });

  if (outgoing.length === 0 && incoming.length === 0) return null;

  const nodeMap = new Map(allNodes.map(n => [n.node_id, n]));

  return (
    <section className="border-t border-forge-border pt-3 mt-2">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 py-1 text-left hover:bg-forge-bg/30 transition-colors rounded"
      >
        <GitBranch size={12} className="text-forge-muted shrink-0" />
        <h4 className="text-[10px] font-mono font-bold uppercase tracking-widest text-forge-muted flex-1">
          Trace Links
        </h4>
        <span className="text-[10px] font-mono text-forge-muted/60 mr-1">
          {outgoing.length + incoming.length}
        </span>
        {open
          ? <ChevronDown size={12} className="text-forge-muted shrink-0" />
          : <ChevronRight size={12} className="text-forge-muted shrink-0" />}
      </button>

      {open && (
        <div className="pl-2 mt-1 space-y-3">
          {outgoing.length > 0 && (
            <div>
              <p className="text-[9px] font-mono text-forge-muted/50 uppercase tracking-widest mb-1.5">
                Traces to ({outgoing.length})
              </p>
              <ul className="space-y-1">
                {outgoing.map(id => {
                  const target = nodeMap.get(id);
                  return (
                    <li key={id} className="flex items-center gap-1.5 text-[10px] font-mono">
                      <span className="text-forge-accent shrink-0">→</span>
                      <span className="font-bold text-forge-accent shrink-0">{id}</span>
                      {target && (
                        <span className="truncate">
                          <span className={`${nodeTypeClass(target).split(' ')[0]} opacity-80`}>
                            {target.node_type}
                          </span>
                          <span className="text-forge-muted/60"> · {target.title}</span>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {incoming.length > 0 && (
            <div>
              <p className="text-[9px] font-mono text-forge-muted/50 uppercase tracking-widest mb-1.5">
                Traced by ({incoming.length})
              </p>
              <ul className="space-y-1">
                {incoming.map(n => (
                  <li key={n.node_id} className="flex items-center gap-1.5 text-[10px] font-mono">
                    <span className="text-forge-success shrink-0">←</span>
                    <span className="font-bold text-forge-success shrink-0">{n.node_id}</span>
                    <span className="truncate">
                      <span className={`${nodeTypeClass(n).split(' ')[0]} opacity-80`}>
                        {n.node_type}
                      </span>
                      <span className="text-forge-muted/60"> · {n.title}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function NodeContextPanel({ node }: { node: GNode }) {
  const [innerOpen, setInnerOpen] = useState(true);
  const [middleOpen, setMiddleOpen] = useState(false);
  const [outerOpen, setOuterOpen] = useState(false);

  const { data, isLoading, isError } = useQuery<ContextBundle>({
    queryKey: ['node-context', node.node_id],
    queryFn: () =>
      fetch(`/api/graph/nodes/${encodeURIComponent(node.node_id)}/context`).then((r) => {
        if (!r.ok) throw new Error('Context fetch failed');
        return r.json();
      }),
    staleTime: 30_000,
  });

  return (
    <section className="border-t border-forge-border pt-3 mt-2">
      <div className="flex items-center gap-2 mb-2">
        <Layers size={12} className="text-forge-muted" />
        <h4 className="text-[10px] font-mono font-bold uppercase tracking-widest text-forge-muted">
          Context Resolution
        </h4>
        {isLoading && <RefreshCw size={10} className="text-forge-muted animate-spin ml-auto" />}
      </div>

      {isError && (
        <p className="text-[10px] font-mono text-forge-error/70">Failed to load context.</p>
      )}

      <TraceLinksSection node={node} />

      {data && (
        <div className="space-y-1">
          {/* INNER */}
          <TierHeader
            label="Inner"
            count={data.inner.length}
            open={innerOpen}
            onToggle={() => setInnerOpen((v) => !v)}
          />
          {innerOpen && (
            <div className="pl-4">
              {data.inner.length === 0 ? (
                <p className="text-[10px] font-mono text-forge-muted/40 italic">No inner context.</p>
              ) : (
                <InnerSection entries={data.inner} />
              )}
            </div>
          )}

          {/* MIDDLE */}
          <TierHeader
            label="Middle"
            count={data.middle.length}
            open={middleOpen}
            onToggle={() => setMiddleOpen((v) => !v)}
          />
          {middleOpen && (
            <div className="pl-4">
              {data.middle.length === 0 ? (
                <p className="text-[10px] font-mono text-forge-muted/40 italic">No siblings — this node is the only child of its parent.</p>
              ) : (
                <MiddleSection entries={data.middle} />
              )}
            </div>
          )}

          {/* OUTER */}
          <TierHeader
            label="Outer"
            count={data.outer.length}
            open={outerOpen}
            onToggle={() => setOuterOpen((v) => !v)}
          />
          {outerOpen && (
            <div className="pl-4">
              {data.outer.length === 0 ? (
                <p className="text-[10px] font-mono text-forge-muted/40 italic">No outer context.</p>
              ) : (
                <OuterSection entries={data.outer} />
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
