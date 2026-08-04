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

function GraphNodeComponent({ data, selected }: NodeProps) {
  const { label, nodeType, color, highlighted, dimmed } = data as {
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

  return (
    <>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div
        style={{
          background: highlighted ? `${color}25` : `${color}15`,
          borderColor,
          borderWidth,
          boxShadow: shadow,
          opacity,
          transition: 'opacity 0.2s, box-shadow 0.2s, border-color 0.2s',
        }}
        className="rounded-md border px-2 py-1 min-w-[130px] max-w-[170px]"
      >
        <div className="text-[8px] font-bold font-mono uppercase tracking-wider" style={{ color }}>
          {nodeType.replace('_', ' ')}
        </div>
        <div className="text-[10px] font-mono text-white/80 leading-tight truncate mt-0.5">
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
            stroke: isTrace ? 'rgba(251,191,36,0.8)' : 'rgba(255,255,255,0.5)',
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
    <div style={{ width, height }} className="bg-[#0d1117]">
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
        <Background color="#21262d" gap={20} />
        <Controls showInteractive={false} style={{ background: '#161b22', borderColor: '#30363d' }} />
        <MiniMap
          nodeColor={(n) => (n.data?.color as string) ?? '#94a3b8'}
          style={{ background: '#0d1117', border: '1px solid #21262d' }}
          maskColor="rgba(0,0,0,0.7)"
        />
      </ReactFlow>
    </div>
  );
});
