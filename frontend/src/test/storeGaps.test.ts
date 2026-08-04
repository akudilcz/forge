/**
 * Zustand store — mergeGaps
 *
 * Every GAP_LIST_UPDATE frame from the WebSocket flows through this reducer, and
 * it alone decides what the operator sees as outstanding work versus recently
 * completed work. The backend sends the *current* gap list on each frame, not a
 * delta, so `mergeGaps` has to derive the delta itself: which gaps survived,
 * which disappeared (and are therefore resolved), and which are new.
 *
 * Two details carry the whole design and are what these tests pin down:
 *
 *  1. Identity is the composite `${type}:${node_id}`, not `node_id` alone. One
 *     node legitimately carries several gaps of different types at once, so
 *     keying on node_id would collapse them and make gaps vanish from the UI.
 *  2. `resolvedGaps` is capped at 50, newest first. Without the cap a long build
 *     grows the array without bound.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { useStore, type Gap } from '@/store';

function gap(type: string, nodeId: string, priority = 1): Gap {
  return {
    type: type as Gap['type'],
    priority,
    node_id: nodeId,
    description: `${type} on ${nodeId}`,
  };
}

/** Key used by the reducer, duplicated here so the test states it explicitly. */
const key = (g: Gap) => `${g.type}:${g.node_id}`;

describe('store.mergeGaps', () => {
  beforeEach(() => {
    useStore.setState({ gaps: [], resolvedGaps: [] });
  });

  const merge = (incoming: Gap[]) => useStore.getState().mergeGaps(incoming);
  const state = () => useStore.getState();

  describe('new gaps', () => {
    it('appends gaps into an empty store', () => {
      merge([gap('UNCOVERED_PARA', 'PARA-1'), gap('UNTESTED_LLR', 'LLR-1')]);

      expect(state().gaps.map(key)).toEqual([
        'UNCOVERED_PARA:PARA-1',
        'UNTESTED_LLR:LLR-1',
      ]);
      expect(state().resolvedGaps).toEqual([]);
    });

    it('appends genuinely new gaps after the retained ones', () => {
      merge([gap('UNCOVERED_PARA', 'PARA-1')]);
      merge([gap('UNCOVERED_PARA', 'PARA-1'), gap('UNCOVERED_PARA', 'PARA-2')]);

      expect(state().gaps.map(key)).toEqual([
        'UNCOVERED_PARA:PARA-1',
        'UNCOVERED_PARA:PARA-2',
      ]);
      expect(state().resolvedGaps).toEqual([]);
    });
  });

  describe('resolution', () => {
    it('moves a disappeared gap into resolvedGaps', () => {
      merge([gap('UNCOVERED_PARA', 'PARA-1'), gap('UNTESTED_LLR', 'LLR-1')]);
      merge([gap('UNCOVERED_PARA', 'PARA-1')]);

      expect(state().gaps.map(key)).toEqual(['UNCOVERED_PARA:PARA-1']);
      expect(state().resolvedGaps.map(key)).toEqual(['UNTESTED_LLR:LLR-1']);
    });

    it('resolves everything when the incoming list is empty', () => {
      merge([gap('UNCOVERED_PARA', 'PARA-1'), gap('UNTESTED_LLR', 'LLR-1')]);
      merge([]);

      expect(state().gaps).toEqual([]);
      expect(state().resolvedGaps).toHaveLength(2);
    });

    it('puts the most recently resolved gaps first', () => {
      merge([gap('A', 'N-1'), gap('B', 'N-2')]);
      merge([gap('B', 'N-2')]); // A:N-1 resolves
      merge([]); //                B:N-2 resolves

      expect(state().resolvedGaps.map(key)).toEqual(['B:N-2', 'A:N-1']);
    });

    it('caps resolvedGaps at 50', () => {
      const many = Array.from({ length: 60 }, (_, i) => gap('UNCOVERED_PARA', `PARA-${i}`));
      merge(many);
      merge([]);

      expect(state().resolvedGaps).toHaveLength(50);
      // Newest-first ordering means the first 50 of the resolved batch survive.
      expect(state().resolvedGaps[0].node_id).toBe('PARA-0');
    });

    it('drops the oldest entries when the cap is exceeded across merges', () => {
      for (let i = 0; i < 55; i++) {
        merge([gap('UNCOVERED_PARA', `PARA-${i}`)]);
        merge([]);
      }

      expect(state().resolvedGaps).toHaveLength(50);
      expect(state().resolvedGaps[0].node_id).toBe('PARA-54');
      expect(state().resolvedGaps.map(g => g.node_id)).not.toContain('PARA-0');
    });
  });

  describe('composite identity', () => {
    it('keeps two different gap types on the same node distinct', () => {
      // Keying on node_id alone would collapse these into one entry and make a
      // real outstanding gap invisible in the UI.
      merge([gap('UNCOVERED_PARA', 'NODE-1'), gap('UNTESTED_LLR', 'NODE-1')]);

      expect(state().gaps).toHaveLength(2);
      expect(state().gaps.map(key).sort()).toEqual([
        'UNCOVERED_PARA:NODE-1',
        'UNTESTED_LLR:NODE-1',
      ]);
    });

    it('resolves only the matching type when one of two gaps on a node clears', () => {
      merge([gap('UNCOVERED_PARA', 'NODE-1'), gap('UNTESTED_LLR', 'NODE-1')]);
      merge([gap('UNTESTED_LLR', 'NODE-1')]);

      expect(state().gaps.map(key)).toEqual(['UNTESTED_LLR:NODE-1']);
      expect(state().resolvedGaps.map(key)).toEqual(['UNCOVERED_PARA:NODE-1']);
    });

    it('treats the same type on different nodes as distinct', () => {
      merge([gap('UNCOVERED_PARA', 'PARA-1'), gap('UNCOVERED_PARA', 'PARA-2')]);
      expect(state().gaps).toHaveLength(2);
    });
  });

  describe('idempotency and stability', () => {
    it('is a no-op when the same list arrives twice', () => {
      const list = [gap('UNCOVERED_PARA', 'PARA-1'), gap('UNTESTED_LLR', 'LLR-1')];
      merge(list);
      const afterFirst = state().gaps.map(key);

      merge(list);

      expect(state().gaps.map(key)).toEqual(afterFirst);
      expect(state().resolvedGaps).toEqual([]);
    });

    it('does not resurrect a resolved gap into gaps when it reappears', () => {
      // A gap that returns is genuinely outstanding again and belongs in `gaps`;
      // the historical resolved entry is kept as a record of the earlier close.
      merge([gap('UNCOVERED_PARA', 'PARA-1')]);
      merge([]);
      merge([gap('UNCOVERED_PARA', 'PARA-1')]);

      expect(state().gaps.map(key)).toEqual(['UNCOVERED_PARA:PARA-1']);
      expect(state().resolvedGaps.map(key)).toEqual(['UNCOVERED_PARA:PARA-1']);
    });

    it('does not mutate the incoming array', () => {
      const incoming = [gap('UNCOVERED_PARA', 'PARA-1')];
      const snapshot = JSON.stringify(incoming);
      merge(incoming);
      expect(JSON.stringify(incoming)).toBe(snapshot);
    });

    it('preserves gap fields verbatim on retained entries', () => {
      const g = gap('UNCOVERED_PARA', 'PARA-1', 7);
      g.context = { detail: 'why' };
      merge([g]);
      merge([g]);

      expect(state().gaps[0].priority).toBe(7);
      expect(state().gaps[0].context).toEqual({ detail: 'why' });
    });
  });

  describe('full turnover', () => {
    it('handles a completely different incoming set', () => {
      merge([gap('A', 'N-1'), gap('B', 'N-2')]);
      merge([gap('C', 'N-3'), gap('D', 'N-4')]);

      expect(state().gaps.map(key)).toEqual(['C:N-3', 'D:N-4']);
      expect(state().resolvedGaps.map(key).sort()).toEqual(['A:N-1', 'B:N-2']);
    });
  });
});
