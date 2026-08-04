/**
 * traceHighlight — buildRelatedNodeIds
 *
 * This helper decides what lights up in the Graph Inspector when you select a
 * node. It is the only place that unifies the graph's two distinct relationship
 * kinds: structural containment (`parent_id`) and traceability (`trace_to`).
 * Both directions matter — a requirement should highlight the tests that trace
 * to it just as much as the module it lives in — and the reverse-trace scan is
 * the part most easily lost in a refactor, because forward traces alone look
 * plausible on screen.
 */

import { describe, expect, it } from 'vitest';

import { buildRelatedNodeIds } from '@/lib/traceHighlight';
import type { GNode } from '@/components/NodeTablePanel';

/** Minimal GNode; only the fields buildRelatedNodeIds reads are meaningful. */
function node(partial: Partial<GNode> & { node_id: string }): GNode {
  return {
    node_type: 'LLR',
    title: partial.node_id,
    parent_id: null,
    trace_to: [],
    ...partial,
  } as GNode;
}

describe('buildRelatedNodeIds', () => {
  describe('empty results', () => {
    it('returns an empty set when nothing is selected', () => {
      const nodes = [node({ node_id: 'A' }), node({ node_id: 'B', parent_id: 'A' })];
      expect(buildRelatedNodeIds(null, nodes)).toEqual(new Set());
    });

    it('returns an empty set when the selected id is not in the node list', () => {
      const nodes = [node({ node_id: 'A' })];
      expect(buildRelatedNodeIds('MISSING', nodes)).toEqual(new Set());
    });

    it('returns an empty set for an isolated node', () => {
      const nodes = [node({ node_id: 'A' }), node({ node_id: 'B' })];
      expect(buildRelatedNodeIds('A', nodes)).toEqual(new Set());
    });

    it('handles an empty node list', () => {
      expect(buildRelatedNodeIds('A', [])).toEqual(new Set());
    });
  });

  describe('structural relationships', () => {
    it('includes the parent', () => {
      const nodes = [node({ node_id: 'PARENT' }), node({ node_id: 'CHILD', parent_id: 'PARENT' })];
      expect(buildRelatedNodeIds('CHILD', nodes)).toEqual(new Set(['PARENT']));
    });

    it('includes every direct child', () => {
      const nodes = [
        node({ node_id: 'P' }),
        node({ node_id: 'C1', parent_id: 'P' }),
        node({ node_id: 'C2', parent_id: 'P' }),
        node({ node_id: 'OTHER' }),
      ];
      expect(buildRelatedNodeIds('P', nodes)).toEqual(new Set(['C1', 'C2']));
    });

    it('does not include grandchildren — highlighting is one hop, not a subtree', () => {
      const nodes = [
        node({ node_id: 'P' }),
        node({ node_id: 'C', parent_id: 'P' }),
        node({ node_id: 'G', parent_id: 'C' }),
      ];
      const related = buildRelatedNodeIds('P', nodes);
      expect(related.has('C')).toBe(true);
      expect(related.has('G')).toBe(false);
    });
  });

  describe('traceability relationships', () => {
    it('includes outgoing trace_to targets', () => {
      const nodes = [
        node({ node_id: 'CASE-1', trace_to: ['LLR-1', 'LLR-2'] }),
        node({ node_id: 'LLR-1' }),
        node({ node_id: 'LLR-2' }),
      ];
      expect(buildRelatedNodeIds('CASE-1', nodes)).toEqual(new Set(['LLR-1', 'LLR-2']));
    });

    it('includes incoming reverse traces', () => {
      // Selecting a requirement must reveal the tests that cover it. Only the
      // reverse scan can find these — LLR-1 itself records nothing about CASE-1.
      const nodes = [
        node({ node_id: 'LLR-1' }),
        node({ node_id: 'CASE-1', trace_to: ['LLR-1'] }),
        node({ node_id: 'CASE-2', trace_to: ['LLR-1'] }),
        node({ node_id: 'CASE-3', trace_to: ['LLR-9'] }),
      ];
      expect(buildRelatedNodeIds('LLR-1', nodes)).toEqual(new Set(['CASE-1', 'CASE-2']));
    });

    it('includes a trace target even when that node is absent from the list', () => {
      // The table may be filtered to one node type; a dangling target should
      // still be reported rather than silently dropped.
      const nodes = [node({ node_id: 'CASE-1', trace_to: ['LLR-GONE'] })];
      expect(buildRelatedNodeIds('CASE-1', nodes)).toEqual(new Set(['LLR-GONE']));
    });

    it('tolerates a missing trace_to field', () => {
      const nodes = [
        { node_id: 'A', node_type: 'LLR', title: 'A', parent_id: null } as unknown as GNode,
        node({ node_id: 'B', parent_id: 'A' }),
      ];
      expect(() => buildRelatedNodeIds('A', nodes)).not.toThrow();
      expect(buildRelatedNodeIds('A', nodes)).toEqual(new Set(['B']));
    });
  });

  describe('combined', () => {
    it('unions parent, children, forward traces and reverse traces', () => {
      const nodes = [
        node({ node_id: 'MOD' }),
        node({ node_id: 'SEL', parent_id: 'MOD', trace_to: ['HLR-1'] }),
        node({ node_id: 'KID', parent_id: 'SEL' }),
        node({ node_id: 'HLR-1' }),
        node({ node_id: 'CASE-1', trace_to: ['SEL'] }),
        node({ node_id: 'UNRELATED', parent_id: 'MOD' }),
      ];
      expect(buildRelatedNodeIds('SEL', nodes)).toEqual(
        new Set(['MOD', 'KID', 'HLR-1', 'CASE-1']),
      );
    });

    it('excludes the selected node even when it is its own trace target', () => {
      // The selected node carries its own distinct style, so including it would
      // make the selection indistinguishable from its neighbours.
      const nodes = [node({ node_id: 'SELF', trace_to: ['SELF'] })];
      expect(buildRelatedNodeIds('SELF', nodes).has('SELF')).toBe(false);
    });

    it('excludes a self-parenting node', () => {
      const nodes = [node({ node_id: 'SELF', parent_id: 'SELF' })];
      expect(buildRelatedNodeIds('SELF', nodes)).toEqual(new Set());
    });

    it('deduplicates a node related by more than one path', () => {
      // X is both the parent and a trace target; a Set must collapse it to one.
      const nodes = [
        node({ node_id: 'X' }),
        node({ node_id: 'SEL', parent_id: 'X', trace_to: ['X'] }),
      ];
      expect(buildRelatedNodeIds('SEL', nodes)).toEqual(new Set(['X']));
    });
  });

  describe('purity', () => {
    it('does not mutate the input nodes', () => {
      const nodes = [
        node({ node_id: 'A', trace_to: ['B'] }),
        node({ node_id: 'B', parent_id: 'A' }),
      ];
      const snapshot = JSON.stringify(nodes);
      buildRelatedNodeIds('A', nodes);
      expect(JSON.stringify(nodes)).toBe(snapshot);
    });

    it('is deterministic across repeated calls', () => {
      const nodes = [
        node({ node_id: 'A', trace_to: ['B'] }),
        node({ node_id: 'B' }),
        node({ node_id: 'C', trace_to: ['A'] }),
      ];
      const first = buildRelatedNodeIds('A', nodes);
      const second = buildRelatedNodeIds('A', nodes);
      expect([...first].sort()).toEqual([...second].sort());
    });
  });
});
