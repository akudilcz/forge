/**
 * Graph Inspector — 3D force-directed graph + node table, bidirectional selection.
 *
 * Layout:
 *   Left  (35%) — NodeTablePanel in list-only controlled mode
 *   Right (65%) — TreeGraphView (SVG top-down tree)
 *
 * Selecting a row in the table focuses the tree view on that node.
 * Clicking a node in the tree selects it and opens the detail panel.
 *
 * Node type filter: click type chips in the stats bar to show/hide types in
 * both the table and the 3D graph.
 */

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Network, RefreshCw, Layers, GitBranch, Workflow, Filter } from 'lucide-react';
import { NodeTablePanel, type GNode, TYPE_COLOR } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { TreeGraphView, type TreeGraphHandle, type LinkMode } from '@/components/TreeGraphView';
import { ResizableSplit } from '@/components/ResizableSplit';
import { buildRelatedNodeIds } from '@/lib/traceHighlight';

// ── Type filter / stats bar ────────────────────────────────────────────────────

function TypeFilterBar({
  nodes,
  hiddenTypes,
  onToggle,
}: {
  nodes: GNode[];
  hiddenTypes: Set<string>;
  onToggle: (type: string) => void;
}) {
  const byType = useMemo(() =>
    Object.entries(nodes.reduce<Record<string, number>>((acc, n) => {
      acc[n.node_type] = (acc[n.node_type] ?? 0) + 1;
      return acc;
    }, {})).sort((a, b) => b[1] - a[1]),
    [nodes],
  );

  const allVisible = hiddenTypes.size === 0;

  const handleAll = () => {
    if (allVisible) {
      // Hide all types
      byType.forEach(([t]) => { if (!hiddenTypes.has(t)) onToggle(t); });
    } else {
      // Show all types
      byType.forEach(([t]) => { if (hiddenTypes.has(t)) onToggle(t); });
    }
  };

  return (
    <div className="flex items-center gap-1.5 px-4 py-2 border-b border-forge-border bg-forge-bg/30 shrink-0 overflow-x-auto">
      <span className="text-xs font-mono text-forge-muted shrink-0">
        <span className="text-forge-text font-bold">{nodes.length}</span> nodes
      </span>
      <span className="text-forge-border shrink-0 px-1">|</span>
      <button
        onClick={handleAll}
        className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 transition-colors ${
          allVisible
            ? 'text-forge-accent bg-forge-accent/10 border-forge-accent/30'
            : 'text-forge-muted bg-forge-surface border-forge-border hover:text-forge-text'
        }`}
        title={allVisible ? 'Hide all node types' : 'Show all node types'}
      >
        all
      </button>
      {byType.map(([type, count]) => {
        const hidden = hiddenTypes.has(type);
        const colorClass = TYPE_COLOR[type] ?? 'text-slate-400 bg-slate-400/15 border-slate-400/30';
        return (
          <button
            key={type}
            onClick={() => onToggle(type)}
            className={`flex items-center gap-1 text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border transition-all ${
              hidden
                ? 'opacity-25 bg-forge-surface border-forge-border text-forge-muted'
                : colorClass
            }`}
            title={hidden ? `Show ${type}` : `Hide ${type}`}
          >
            <span>{type}</span>
            <span className="opacity-60">{count}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── 3D canvas with resize tracking ───────────────────────────────────────────

function GraphCanvas({
  nodes,
  allNodes,
  selectedNodeId,
  highlightedNodeIds,
  onNodeClick,
  graphRef,
  linkMode,
}: {
  nodes: GNode[];
  allNodes: GNode[];
  selectedNodeId: string | null;
  highlightedNodeIds: Set<string>;
  onNodeClick: (node: GNode) => void;
  graphRef: React.RefObject<TreeGraphHandle>;
  linkMode: LinkMode;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 600 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      setDims({ width: Math.floor(width), height: Math.floor(height) });
    });
    ro.observe(el);
    setDims({ width: Math.floor(el.clientWidth), height: Math.floor(el.clientHeight) });
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="flex-1 min-w-0 min-h-0 overflow-hidden bg-[#0d1117]">
      {dims.width > 0 && dims.height > 0 && (
        <TreeGraphView
          ref={graphRef}
          nodes={nodes}
          selectedNodeId={selectedNodeId}
          highlightedNodeIds={highlightedNodeIds}
          onNodeClick={onNodeClick}
          width={dims.width}
          height={dims.height}
          linkMode={linkMode}
          allNodes={allNodes}
        />
      )}
    </div>
  );
}

// ── Main dashboard ────────────────────────────────────────────────────────────

export function GraphInspector() {
  const { data: nodes = [], isLoading, refetch } = useQuery<GNode[]>({
    queryKey: ['graph-all-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [linkMode, setLinkMode]             = useState<LinkMode>('both');
  const [hiddenTypes, setHiddenTypes]       = useState<Set<string>>(new Set());
  const [traceableOnly, setTraceableOnly]   = useState(false);
  const graphRef = useRef<TreeGraphHandle | null>(null);

  // Node types shown when "traceable only" filter is active
  const TRACEABLE_TYPES = new Set([
    'PROJECT', 'DOCUMENT', 'PARA',
    'HLR', 'LLR', 'ARCHITECTURE', 'MODULE', 'CONTRACT', 'DESIGN', 'CODE',
    'SUITE', 'CASE_HLR', 'CASE_LLR', 'TEST', 'RESULT',
  ]);

  const toggleType = useCallback((type: string) => {
    setHiddenTypes(prev => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type); else next.add(type);
      return next;
    });
  }, []);

  const filteredNodes = useMemo(
    () => nodes.filter(n => {
      if (hiddenTypes.has(n.node_type)) return false;
      if (traceableOnly && !TRACEABLE_TYPES.has(n.node_type)) return false;
      return true;
    }),
    [nodes, hiddenTypes, traceableOnly],
  );

  const highlightedNodeIds = useMemo(
    () => buildRelatedNodeIds(selectedNodeId, nodes),
    [selectedNodeId, nodes],
  );

  const handleNodeClick = useCallback((node: GNode) => {
    setSelectedNodeId(prev => prev === node.node_id ? null : node.node_id);
  }, []);

  const handleTableClick = useCallback((node: GNode) => {
    setSelectedNodeId(prev => {
      const next = prev === node.node_id ? null : node.node_id;
      if (next) graphRef.current?.focusNode(next);
      return next;
    });
  }, []);

  const rightPanel = (
    <div className="flex flex-col min-h-0 h-full p-3 pl-1.5">
      {/* Link mode toggle */}
      <div className="flex items-center gap-1 mb-1.5 shrink-0">
        {(['both', 'structural', 'trace'] as LinkMode[]).map(mode => {
          const labels: Record<LinkMode, { icon: React.ReactNode; text: string }> = {
            both:       { icon: <Workflow size={11} />, text: 'Both' },
            structural: { icon: <GitBranch size={11} />, text: 'Structural' },
            trace:      { icon: <Network size={11} />, text: 'Full Trace' },
          };
          const active = linkMode === mode;
          return (
            <button
              key={mode}
              onClick={() => setLinkMode(mode)}
              className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono border transition-colors ${
                active
                  ? 'bg-forge-accent/20 border-forge-accent/40 text-forge-accent'
                  : 'bg-forge-surface border-forge-border text-forge-muted hover:text-forge-text'
              }`}
            >
              {labels[mode].icon}
              {labels[mode].text}
            </button>
          );
        })}
        <label className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-mono text-forge-muted hover:text-forge-text cursor-pointer select-none ml-1">
          <input
            type="checkbox"
            checked={traceableOnly}
            onChange={e => setTraceableOnly(e.target.checked)}
            className="w-3 h-3 accent-forge-accent rounded"
          />
          <Filter size={10} />
          Traceable only
        </label>
      </div>
      <div className="flex-1 rounded-xl border border-forge-border overflow-hidden flex min-h-0">
        <GraphCanvas
          nodes={filteredNodes}
          allNodes={nodes}
          selectedNodeId={selectedNodeId}
          highlightedNodeIds={highlightedNodeIds}
          onNodeClick={handleNodeClick}
          graphRef={graphRef}
          linkMode={linkMode}
        />
      </div>
    </div>
  );

  return (
    <div className="h-full flex flex-col animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-forge-border shrink-0">
        <div className="flex items-center gap-3">
          <Network size={20} className="text-forge-accent" />
          <div>
            <h1 className="text-xl font-bold font-mono text-forge-text">Graph Inspector</h1>
            <p className="text-xs text-forge-muted font-mono">
              3D view — click a node to inspect · select in list to focus · click type chips to filter
            </p>
          </div>
        </div>
        <button
          onClick={() => refetch()}
          className="p-2 rounded-lg bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-text transition-colors"
          title="Refresh"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Type filter bar */}
      {nodes.length > 0 && (
        <TypeFilterBar nodes={nodes} hiddenTypes={hiddenTypes} onToggle={toggleType} />
      )}

      {/* Empty state */}
      {nodes.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-forge-muted/40">
          <Layers size={40} className="mb-3 opacity-20" />
          <p className="font-mono text-sm">Graph is empty.</p>
          <p className="font-mono text-xs mt-1 opacity-60">Start the loop to populate the graph.</p>
        </div>
      )}

      {/* Main content: table (35%) + 3D canvas (65%) */}
      {nodes.length > 0 && (
        <div className="flex-1 min-h-0">
          <ResizableSplit
            initialSplit={35}
            minLeft={15}
            maxLeft={75}
            storageKey="graph-inspector"
            left={
              <div className="flex flex-col min-h-0 h-full p-3 pr-1.5">
                <NodeTablePanel
                  nodes={filteredNodes}
                  isLoading={isLoading}
                  title="Nodes"
                  icon={<Network size={14} />}
                  onRefresh={refetch}
                  emptyMessage="Graph is empty. Start the loop to populate."
                  extraDetail={(node) => <NodeContextPanel node={node} />}
                  externalSelectedId={selectedNodeId}
                  onNodeClick={handleTableClick}
                />
              </div>
            }
            right={rightPanel}
          />
        </div>
      )}
    </div>
  );
}
