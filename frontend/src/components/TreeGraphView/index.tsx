/**
 * TreeGraphView — React Flow top-down tree. All nodes visible, dagre layout.
 */

import { useCallback, forwardRef, useImperativeHandle, useRef, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type NodeProps,
  type ReactFlowInstance,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { GNode } from '@/components/NodeTablePanel';
import { useTreeLayout } from './useTreeLayout';

export type LinkMode = 'both' | 'structural' | 'trace';

export interface TreeGraphHandle {
  focusNode: (nodeId: string) => void;
}

// ── Node ──────────────────────────────────────────────────────────────────────

/**
 * Shape language per artifact family \u2014 corner radius distinguishes the
 * hierarchy tier at a glance, in addition to the type hue:
 *   source docs (DOCUMENT/PARA)        \u2192 soft rounded
 *   requirements (HLR/LLR)            \u2192 sharp corners
 *   design tier (ARCH/MODULE/\u2026)       \u2192 medium rounded
 *   verification (SUITE/CASE/TEST/\u2026)  \u2192 pill ends
 */
const TYPE_SHAPE: Record<string, string> = {
  PROJECT: 'rounded-lg', DOCUMENT: 'rounded-lg', PARA: 'rounded-lg',
  HLR: 'rounded-[2px]', LLR: 'rounded-[2px]', REQ: 'rounded-[2px]',
  ARCHITECTURE: 'rounded-md', MODULE: 'rounded-md', CONTRACT: 'rounded-md',
  CLASS: 'rounded-md', DESIGN: 'rounded-md', CODE: 'rounded-md',
  SUITE: 'rounded-full', CASE_HLR: 'rounded-full', CASE_LLR: 'rounded-full',
  TEST: 'rounded-full', RESULT: 'rounded-full',
};

function GraphNodeComponent({ data, selected }: NodeProps) {
  const { label, nodeType, color, gnode, highlighted, dimmed } = data as {
    label: string; nodeType: string; color: string; gnode: GNode;
    highlighted?: boolean; dimmed?: boolean;
  };
  const truncLabel = typeof label === 'string' && label.length > 20
    ? label.slice(0, 19) + '\u2026' : label;

  const borderColor = selected ? color : highlighted ? `${color}90` : `${color}50`;
  const borderWidth = selected ? 2 : highlighted ? 2 : 1;
  const shadow = selected
    ? `0 0 12px ${color}40`
    : highlighted
      ? `0 0 8px ${color}25`
      : 'none';
  const opacity = dimmed ? 0.3 : 1;
  const shape = TYPE_SHAPE[nodeType] ?? 'rounded-md';
  const traceCount = gnode?.trace_to?.length ?? 0;
  const hoverContext = `${nodeType} \u00b7 ${gnode?.node_id ?? ''}\n${label}${
    traceCount > 0 ? `\ntraces \u2192 ${traceCount}` : ''
  }`;

  return (
    <>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div
        title={hoverContext}
        style={{
          background: highlighted ? `${color}25` : `${color}15`,
          borderColor,
          borderWidth,
          borderLeftWidth: Math.max(borderWidth, 3),
          boxShadow: shadow,
          opacity,
          transition: 'opacity 0.2s, box-shadow 0.2s, border-color 0.2s',
        }}
        className={`border px-2.5 py-1 min-w-[130px] max-w-[170px] ${shape}`}
      >
        <div className="flex items-baseline gap-1">
          <span className="text-[8px] font-bold font-mono uppercase tracking-wider" style={{ color }}>
            {nodeType.replace('_', ' ')}
          </span>
          {traceCount > 0 && (
            <span className="text-[7px] font-mono opacity-60" style={{ color }}>
              \u21e2{traceCount}
            </span>
          )}
        </div>
        <div
          className="text-[10px] font-mono leading-tight truncate mt-0.5"
          style={{ color: 'var(--graph-node-label)' }}
        >
          {truncLabel}
        </div>
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </>
  );
}

const nodeTypes = { graphNode: GraphNodeComponent };

// ── Main ──────────────────────────────────────────────────────────────────────

export const TreeGraphView = forwardRef<TreeGraphHandle, {
  nodes: GNode[];
  selectedNodeId: string | null;
  highlightedNodeIds?: Set<string>;
  onNodeClick: (node: GNode) => void;
  width: number;
  height: number;
  linkMode?: LinkMode;
  allNodes?: GNode[];
}>(function TreeGraphView({ nodes: gnodes, selectedNodeId, highlightedNodeIds, onNodeClick, width, height, linkMode = 'both', allNodes }, ref) {
  const { nodes: layoutNodes, edges: layoutEdges } = useTreeLayout(gnodes, allNodes ?? gnodes);
  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges);
  const rfInstance = useRef<ReactFlowInstance | null>(null);
  const prevKeyRef = useRef('');

  const hlSet = highlightedNodeIds ?? new Set<string>();
  const hasSelection = !!selectedNodeId;

  useEffect(() => {
    // Only rebuild when node IDs, selection, or linkMode actually change
    const hlKey = [...hlSet].sort().join(',');
    const nodeKey = gnodes.map(n => n.node_id).join(',') + '|' + selectedNodeId + '|' + linkMode + '|' + hlKey;
    if (nodeKey === prevKeyRef.current) return;
    prevKeyRef.current = nodeKey;

    setNodes(layoutNodes.map(n => {
      const isSelected = n.id === selectedNodeId;
      const isHighlighted = hlSet.has(n.id);
      const isDimmed = hasSelection && !isSelected && !isHighlighted;
      return {
        ...n,
        selected: isSelected,
        data: { ...n.data, highlighted: isHighlighted, dimmed: isDimmed },
      };
    }));

    const filtered = layoutEdges.filter(e => {
      const isTrace = e.id.startsWith('t-');
      if (linkMode === 'structural') return !isTrace;
      if (linkMode === 'trace') return isTrace;
      return true;
    }).map(e => {
      if (!hasSelection) return e;
      // An edge is "active" when it connects the selected node to a highlighted node
      const srcIsSelected = e.source === selectedNodeId;
      const tgtIsSelected = e.target === selectedNodeId;
      const srcIsHighlighted = hlSet.has(e.source);
      const tgtIsHighlighted = hlSet.has(e.target);
      const isActive =
        (srcIsSelected && tgtIsHighlighted) ||
        (tgtIsSelected && srcIsHighlighted);
      const isTrace = e.id.startsWith('t-');
      if (isActive) {
        return {
          ...e,
          style: {
            ...e.style,
            stroke: isTrace ? 'rgba(251,191,36,0.8)' : 'var(--graph-edge-active)',
            strokeWidth: 2,
          },
          zIndex: 20,
        };
      }
      return {
        ...e,
        style: { ...e.style, opacity: 0.15 },
      };
    });
    setEdges(filtered);
  }, [layoutNodes, layoutEdges, selectedNodeId, linkMode, gnodes, setNodes, setEdges, hlSet, hasSelection]);

  useEffect(() => {
    const t = setTimeout(() => rfInstance.current?.fitView({ padding: 0.15, duration: 300 }), 100);
    return () => clearTimeout(t);
  }, [gnodes.length]);

  useImperativeHandle(ref, () => ({
    focusNode(nodeId: string) {
      const node = nodes.find(n => n.id === nodeId);
      if (node && rfInstance.current) {
        rfInstance.current.fitView({ nodes: [node], padding: 2, duration: 400 });
      }
    },
  }), [nodes]);

  const handleNodeClick = useCallback((_: React.MouseEvent, node: { data: Record<string, unknown> }) => {
    const gnode = node.data.gnode as GNode | undefined;
    if (gnode) onNodeClick(gnode);
  }, [onNodeClick]);

  return (
    <div style={{ width, height, background: 'var(--graph-canvas)' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onInit={(inst) => { rfInstance.current = inst; }}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.05}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        colorMode="dark"
      >
        <Background color="var(--graph-grid)" gap={20} />
        <Controls
          showInteractive={false}
          style={{ background: 'rgb(var(--forge-surface))', borderColor: 'rgb(var(--forge-border-strong))' }}
        />
        <MiniMap
          nodeColor={(n) => (n.data?.color as string) ?? '#94a3b8'}
          style={{ background: 'var(--graph-canvas)', border: '1px solid var(--graph-grid)' }}
          maskColor="rgba(0,0,0,0.55)"
        />
      </ReactFlow>
    </div>
  );
});
