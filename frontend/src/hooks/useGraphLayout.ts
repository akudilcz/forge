/**
 * useGraphLayout — computes a simple force-directed layout for a set of
 * graph nodes and edges, returning positioned nodes suitable for SVG rendering.
 *
 * This is a pure-JS implementation with no external layout library dependency.
 */

import { useMemo } from 'react';

export interface RawNode {
  node_id: string;
  title: string;
  node_type?: string;
  [key: string]: unknown;
}

export interface RawEdge {
  source_id?: string;
  source?: string;
  target_id?: string;
  target?: string;
  edge_type?: string;
  relationship?: string;
}

export interface PositionedNode extends RawNode {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface UseGraphLayoutResult {
  nodes: PositionedNode[];
  edges: Array<{ source: PositionedNode; target: PositionedNode; label: string }>;
  width: number;
  height: number;
}

const NODE_W = 120;
const NODE_H = 36;
const V_GAP = 52;

/** Simple layered layout — groups nodes by type-prefix into columns. */
export function useGraphLayout(
  rawNodes: RawNode[],
  rawEdges: RawEdge[],
  containerWidth = 800,
): UseGraphLayoutResult {
  return useMemo(() => {
    if (!rawNodes.length) {
      return { nodes: [], edges: [], width: containerWidth, height: 200 };
    }

    // Group nodes by type prefix
    const groups: Record<string, RawNode[]> = {};
    for (const n of rawNodes) {
      const prefix = (n.node_type ?? n.node_id).split(':')[0] ?? 'other';
      (groups[prefix] ??= []).push(n);
    }

    const prefixes = Object.keys(groups);
    const colCount = Math.max(1, prefixes.length);
    const colWidth = Math.floor(containerWidth / colCount);

    const positioned: PositionedNode[] = [];

    prefixes.forEach((prefix, colIdx) => {
      const colNodes = groups[prefix];
      const colX = colIdx * colWidth + Math.floor((colWidth - NODE_W) / 2);

      colNodes.forEach((node, rowIdx) => {
        positioned.push({
          ...node,
          x: colX,
          y: rowIdx * (NODE_H + V_GAP) + V_GAP,
          width: NODE_W,
          height: NODE_H,
        });
      });
    });

    // Build node lookup
    const byId = Object.fromEntries(positioned.map((n) => [n.node_id, n]));

    const edges = rawEdges.flatMap((e) => {
      const srcId = e.source_id ?? e.source ?? '';
      const tgtId = e.target_id ?? e.target ?? '';
      const src = byId[srcId];
      const tgt = byId[tgtId];
      if (!src || !tgt) return [];
      return [{ source: src, target: tgt, label: e.edge_type ?? e.relationship ?? '' }];
    });

    const maxY = Math.max(...positioned.map((n) => n.y + n.height), 200);
    const height = maxY + V_GAP;

    return { nodes: positioned, edges, width: containerWidth, height };
  }, [rawNodes, rawEdges, containerWidth]);
}
