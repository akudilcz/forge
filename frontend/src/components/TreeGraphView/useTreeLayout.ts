/**
 * useTreeLayout — convert filtered GNode[] to React Flow nodes + edges.
 *
 * Uses a simple approach: dagre for vertical positioning within columns,
 * fixed X position per node type for horizontal column placement.
 */

import { useMemo } from 'react';
import { type Node, type Edge, Position } from '@xyflow/react';
import type { GNode } from '@/components/NodeTablePanel';
import { TYPE_HEX } from '@/lib/nodeColors';

const NODE_HEIGHT = 42;
const COL_WIDTH = 220;  // horizontal spacing between columns

/** Fixed column for each node type — matches the phase pipeline. */
const TYPE_COL: Record<string, number> = {
  PROJECT: 0,
  DOCUMENT: 1, ARCHITECTURE: 1, SUITE: 1,
  PARA: 2, MODULE: 2,
  HLR: 3, CONTRACT: 3,
  LLR: 4,
  DESIGN: 5, CASE_HLR: 5, CASE_LLR: 5,
  CODE: 6, TEST: 6,
  RESULT: 7,
};

/** Walk allNodes parent chains to find nearest visible ancestor. */
function findVisibleAncestor(
  nodeId: string,
  allById: Map<string, GNode>,
  visibleIds: Set<string>,
): string | null {
  let current = allById.get(nodeId);
  while (current?.parent_id) {
    if (visibleIds.has(current.parent_id)) return current.parent_id;
    current = allById.get(current.parent_id);
  }
  return null;
}

export function useTreeLayout(gnodes: GNode[], allNodes: GNode[]) {
  return useMemo(() => {
    if (gnodes.length === 0) return { nodes: [] as Node[], edges: [] as Edge[] };

    const visibleIds = new Set(gnodes.map(n => n.node_id));
    const allById = new Map(allNodes.map(n => [n.node_id, n]));

    // Group visible nodes by their column
    const colGroups = new Map<number, GNode[]>();
    for (const n of gnodes) {
      const col = TYPE_COL[n.node_type] ?? 0;
      const group = colGroups.get(col) ?? [];
      group.push(n);
      colGroups.set(col, group);
    }

    // Assign positions: fixed X per column, evenly spaced Y within each column
    const positions = new Map<string, { x: number; y: number }>();

    // Find the max column height for centering
    let maxColSize = 0;
    for (const [, group] of colGroups) {
      maxColSize = Math.max(maxColSize, group.length);
    }
    const totalHeight = maxColSize * (NODE_HEIGHT + 18);

    // Find which columns are actually used and map to compact X positions
    const usedCols = [...colGroups.keys()].sort((a, b) => a - b);
    const colXMap = new Map<number, number>();
    usedCols.forEach((col, i) => colXMap.set(col, i * COL_WIDTH));

    for (const [col, group] of colGroups) {
      const x = colXMap.get(col) ?? 0;
      const colHeight = group.length * (NODE_HEIGHT + 18);
      const startY = (totalHeight - colHeight) / 2; // center this column

      group.forEach((n, i) => {
        positions.set(n.node_id, {
          x,
          y: startY + i * (NODE_HEIGHT + 18),
        });
      });
    }

    // Build React Flow nodes
    const nodes: Node[] = gnodes.map(n => {
      const pos = positions.get(n.node_id) ?? { x: 0, y: 0 };
      return {
        id: n.node_id,
        type: 'graphNode',
        data: {
          label: n.title || n.node_id,
          nodeType: n.node_type,
          color: TYPE_HEX[n.node_type] ?? '#94a3b8',
          gnode: n,
        },
        position: pos,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        zIndex: 10,
      };
    });

    // Structural edges: nearest visible ancestor
    const structuralEdges: Edge[] = [];
    for (const n of gnodes) {
      const ancestor = findVisibleAncestor(n.node_id, allById, visibleIds);
      if (ancestor) {
        structuralEdges.push({
          id: `e-${ancestor}-${n.node_id}`,
          source: ancestor,
          target: n.node_id,
          type: 'bezier',
          style: { stroke: 'var(--graph-edge)', strokeWidth: 1.2 },
        });
      }
    }

    // Trace edges
    const traceEdges: Edge[] = [];
    for (const n of gnodes) {
      for (const tid of (n.trace_to ?? [])) {
        if (visibleIds.has(tid)) {
          traceEdges.push({
            id: `t-${n.node_id}-${tid}`,
            source: n.node_id,
            target: tid,
            type: 'bezier',
            className: 'trace-edge',
            style: { stroke: 'rgba(251,191,36,0.4)', strokeWidth: 1.2, strokeDasharray: '6 4' },
          });
        }
      }
    }

    return { nodes, edges: [...structuralEdges, ...traceEdges] };
  }, [gnodes, allNodes]);
}
