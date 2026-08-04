/**
 * Traceability dashboard — gap summary strip + full node browser with
 * trace chain injected as extraDetail in the NodeTablePanel.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { GitBranch, AlertTriangle, ChevronRight, RefreshCw } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';

// ── Types ──────────────────────────────────────────────────────────────────────

interface TraceGap {
  gap_type: string;
  node_id: string;
  description: string;
}

interface TraceabilityGaps {
  unimplemented_requirements: TraceGap[];
  untested_implementations: TraceGap[];
  orphan_nodes: TraceGap[];
}

interface TraceLink {
  node_id: string;
  label: string;
}

interface TraceChain {
  node_id: string;
  ancestors: TraceLink[];
  descendants: TraceLink[];
}

// ── Gap section (collapsible, default closed) ──────────────────────────────────

function GapSection({ title, gaps, color }: { title: string; gaps: TraceGap[]; color: string }) {
  const [expanded, setExpanded] = useState(false);
  if (gaps.length === 0) return null;

  return (
    <div className={`rounded-xl border overflow-hidden`} style={{ borderColor: `var(--color-${color}, #444)` }}>
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full p-3 flex items-center gap-3 bg-forge-surface hover:bg-forge-bg/50 transition-colors"
      >
        <AlertTriangle size={13} className={`text-forge-${color} shrink-0`} />
        <span className={`text-xs font-bold font-mono uppercase text-forge-${color}`}>{title}</span>
        <span className={`ml-1 text-[10px] font-mono bg-forge-${color}/10 text-forge-${color} px-2 py-0.5 rounded-full shrink-0`}>
          {gaps.length}
        </span>
        <ChevronRight
          size={13}
          className={`ml-auto text-forge-muted transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      </button>
      {expanded && (
        <div className="divide-y divide-forge-border/30 bg-forge-bg/20">
          {gaps.map((g, i) => (
            <div key={i} className="px-4 py-2.5">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-[10px] font-mono text-forge-muted uppercase">{g.gap_type}</span>
                <span className="text-[10px] font-mono text-forge-accent ml-auto">{g.node_id}</span>
              </div>
              <p className="text-xs font-mono text-forge-text">{g.description}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Trace chain section injected into NodeDetail ───────────────────────────────

function TraceChainSection({ node }: { node: GNode }) {
  const { data, isLoading } = useQuery<TraceChain>({
    queryKey: ['trace-chain', node.node_id],
    queryFn: () =>
      fetch(`/api/graph/nodes/${encodeURIComponent(node.node_id)}/traceability`).then(r => r.json()),
  });

  return (
    <section className="p-4">
      <h4 className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-3">
        Trace Chain
      </h4>

      {isLoading && (
        <div className="flex items-center gap-2 text-forge-muted text-xs font-mono">
          <RefreshCw size={11} className="animate-spin" />
          <span>Loading trace…</span>
        </div>
      )}

      {data && (
        <div className="space-y-3">
          {data.ancestors.length > 0 && (
            <div>
              <p className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-1.5">
                Upstream ({data.ancestors.length})
              </p>
              <div className="space-y-1">
                {data.ancestors.map((a, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-xs font-mono p-2 rounded bg-forge-bg/50 border border-forge-border/50"
                  >
                    <GitBranch size={10} className="text-forge-accent shrink-0" />
                    <span className="text-forge-accent shrink-0">{a.node_id}</span>
                    <span className="truncate text-forge-muted">{a.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 p-2 rounded border-2 border-forge-accent/40 bg-forge-accent/5 text-xs font-mono">
            <GitBranch size={10} className="text-forge-accent shrink-0" />
            <span className="text-forge-accent font-semibold truncate">{node.node_id}</span>
            <span className="text-[10px] font-mono text-forge-muted ml-auto shrink-0">SELECTED</span>
          </div>

          {data.descendants.length > 0 && (
            <div>
              <p className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-1.5">
                Downstream ({data.descendants.length})
              </p>
              <div className="space-y-1">
                {data.descendants.map((d, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 text-xs font-mono p-2 rounded bg-forge-bg/50 border border-forge-border/50"
                  >
                    <GitBranch size={10} className="text-forge-success shrink-0" />
                    <span className="text-forge-success shrink-0">{d.node_id}</span>
                    <span className="truncate text-forge-muted">{d.label}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {data.ancestors.length === 0 && data.descendants.length === 0 && (
            <p className="text-xs font-mono text-forge-muted/50 italic">No trace links for this node.</p>
          )}
        </div>
      )}
    </section>
  );
}

// ── Main dashboard ─────────────────────────────────────────────────────────────

export function Traceability() {
  const { data: gapData, isLoading: gapsLoading, refetch } = useQuery<TraceabilityGaps>({
    queryKey: ['traceability-gaps'],
    queryFn: () => fetch('/api/graph/traceability/gaps').then(r => r.json()),
    refetchInterval: 15_000,
  });

  const { data: allNodes = [], isLoading: nodesLoading, refetch: refetchNodes } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const unimplemented = gapData?.unimplemented_requirements ?? [];
  const untested      = gapData?.untested_implementations  ?? [];
  const orphans       = gapData?.orphan_nodes              ?? [];
  const totalGaps     = unimplemented.length + untested.length + orphans.length;

  function refetchAll() { refetch(); refetchNodes(); }

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Traceability</h1>
          <p className="text-sm text-forge-muted font-mono">
            End-to-end evidence chains from whitepaper to test results.
          </p>
        </div>
        <button
          onClick={refetchAll}
          className="p-2 rounded-lg bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-text transition-colors"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3 shrink-0">
        <div className="p-3 rounded-xl border border-forge-border bg-forge-surface">
          <p className="text-[10px] font-mono text-forge-muted uppercase mb-1">Unimplemented Reqs</p>
          <p className={`text-2xl font-bold font-mono ${unimplemented.length > 0 ? 'text-forge-error' : 'text-forge-success'}`}>
            {gapsLoading ? '…' : unimplemented.length}
          </p>
        </div>
        <div className="p-3 rounded-xl border border-forge-border bg-forge-surface">
          <p className="text-[10px] font-mono text-forge-muted uppercase mb-1">Untested Implementations</p>
          <p className={`text-2xl font-bold font-mono ${untested.length > 0 ? 'text-forge-warning' : 'text-forge-success'}`}>
            {gapsLoading ? '…' : untested.length}
          </p>
        </div>
        <div className="p-3 rounded-xl border border-forge-border bg-forge-surface">
          <p className="text-[10px] font-mono text-forge-muted uppercase mb-1">Orphan Nodes</p>
          <p className={`text-2xl font-bold font-mono ${orphans.length > 0 ? 'text-forge-warning' : 'text-forge-success'}`}>
            {gapsLoading ? '…' : orphans.length}
          </p>
        </div>
      </div>

      {/* Collapsible gap sections */}
      {!gapsLoading && totalGaps > 0 && (
        <div className="space-y-2 shrink-0">
          <GapSection title="Unimplemented Requirements" gaps={unimplemented} color="error" />
          <GapSection title="Untested Implementations"  gaps={untested}      color="warning" />
          <GapSection title="Orphan Nodes"              gaps={orphans}       color="warning" />
        </div>
      )}

      {!gapsLoading && totalGaps === 0 && (
        <div className="bg-forge-success/5 border border-forge-success/20 rounded-xl p-4 flex items-center gap-3 shrink-0">
          <GitBranch size={20} className="text-forge-success shrink-0" />
          <div>
            <p className="text-forge-success font-bold font-mono text-sm">Full traceability achieved</p>
            <p className="text-forge-muted font-mono text-xs mt-0.5">All requirements are traced end-to-end.</p>
          </div>
        </div>
      )}

      {/* Node browser with trace chain detail */}
      <NodeTablePanel
        nodes={allNodes}
        isLoading={nodesLoading}
        title="All Nodes"
        icon={<GitBranch size={14} />}
        onRefresh={refetchNodes}
        emptyMessage="No nodes yet. Run the loop to populate the graph."
        extraDetail={(node) => (
          <>
            <TraceChainSection node={node} />
            <NodeContextPanel node={node} />
          </>
        )}
      />
    </div>
  );
}
