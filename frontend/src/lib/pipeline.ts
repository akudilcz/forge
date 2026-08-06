/**
 * Pipeline derivations — pure helpers behind the pipeline rail and overview.
 */

import type { Gap } from '@/store';
import { GAP_PHASE } from '@/lib/phaseConfig';

/** Count structural gaps per owning phase. Gaps with no phase mapping are ignored. */
export function gapCountsByPhase(gaps: Gap[]): Record<number, number> {
  const counts: Record<number, number> = {};
  for (const gap of gaps) {
    const phase = GAP_PHASE[gap.type];
    if (phase === undefined) continue;
    counts[phase] = (counts[phase] ?? 0) + 1;
  }
  return counts;
}
