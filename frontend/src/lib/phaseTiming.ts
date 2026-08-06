/**
 * Phase wall-time tracking — pure state transitions consumed by the store.
 *
 * The backend does not report per-phase timing, so the client clocks each
 * phase from the websocket status transitions it observes.
 */

import type { PhaseStatus } from '@/store';

export interface PhaseTime {
  startedAt: number | null;
  endedAt: number | null;
}

export type PhaseTimes = Record<number, PhaseTime>;

/** Compute the next timing map after a phase status transition. */
export function nextPhaseTimes(
  prev: PhaseTimes,
  phase: number,
  status: PhaseStatus,
  now: number,
): PhaseTimes {
  const current = prev[phase] ?? { startedAt: null, endedAt: null };

  if (status === 'pending') {
    if (current.startedAt === null && current.endedAt === null) return prev;
    return { ...prev, [phase]: { startedAt: null, endedAt: null } };
  }

  if (status === 'active') {
    // (Re-)entering active restarts the clock only if the phase was not
    // already running — repeated 'active' events keep the original start.
    if (current.startedAt !== null && current.endedAt === null) return prev;
    return { ...prev, [phase]: { startedAt: now, endedAt: null } };
  }

  // complete / awaiting_approval / skipped — stop the clock if it was running.
  if (current.startedAt !== null && current.endedAt === null) {
    return { ...prev, [phase]: { startedAt: current.startedAt, endedAt: now } };
  }
  return prev;
}

/** Elapsed milliseconds for a phase — live phases measure against `now`. */
export function phaseElapsedMs(time: PhaseTime | undefined, now: number): number | null {
  if (!time || time.startedAt === null) return null;
  return (time.endedAt ?? now) - time.startedAt;
}

/** Compact human duration: 0.4s, 12s, 3m 05s, 1h 12m. */
export function formatDuration(ms: number): string {
  if (ms < 0) return '0s';
  const s = ms / 1000;
  if (s < 1) return `${s.toFixed(1)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, '0')}m`;
}
