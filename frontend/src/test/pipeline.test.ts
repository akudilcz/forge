import { describe, it, expect } from 'vitest';
import { gapCountsByPhase } from '@/lib/pipeline';
import type { Gap } from '@/store';

function gap(type: Gap['type'], node_id: string): Gap {
  return { type, priority: 1, node_id, description: 'd' };
}

describe('gapCountsByPhase', () => {
  it('counts structural gaps under their owning phase', () => {
    const counts = gapCountsByPhase([
      gap('UNCOVERED_PARA', 'p1'),
      gap('UNCOVERED_PARA', 'p2'),
      gap('UNTESTED_HLR', 'h1'),
      gap('UNTESTED_LLR', 'l1'),
    ]);
    expect(counts).toEqual({ 3: 2, 10: 2 });
  });

  it('ignores gap types with no phase mapping (quality gaps)', () => {
    const counts = gapCountsByPhase([
      gap('STALE_NODE', 'n1'),
      gap('ORPHAN_NODE', 'n2'),
    ]);
    expect(counts).toEqual({});
  });

  it('returns an empty map for no gaps', () => {
    expect(gapCountsByPhase([])).toEqual({});
  });
});
