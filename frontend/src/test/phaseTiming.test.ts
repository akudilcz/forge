import { describe, it, expect } from 'vitest';
import { nextPhaseTimes, phaseElapsedMs, formatDuration, type PhaseTimes } from '@/lib/phaseTiming';

describe('nextPhaseTimes', () => {
  it('starts the clock when a phase becomes active', () => {
    const next = nextPhaseTimes({}, 3, 'active', 1000);
    expect(next[3]).toEqual({ startedAt: 1000, endedAt: null });
  });

  it('stops the clock when an active phase completes', () => {
    const prev: PhaseTimes = { 3: { startedAt: 1000, endedAt: null } };
    const next = nextPhaseTimes(prev, 3, 'complete', 5000);
    expect(next[3]).toEqual({ startedAt: 1000, endedAt: 5000 });
  });

  it('stops the clock on awaiting_approval', () => {
    const prev: PhaseTimes = { 2: { startedAt: 500, endedAt: null } };
    const next = nextPhaseTimes(prev, 2, 'awaiting_approval', 900);
    expect(next[2]).toEqual({ startedAt: 500, endedAt: 900 });
  });

  it('keeps the original start on repeated active events', () => {
    const prev: PhaseTimes = { 3: { startedAt: 1000, endedAt: null } };
    const next = nextPhaseTimes(prev, 3, 'active', 2000);
    expect(next).toBe(prev);
  });

  it('restarts the clock when a completed phase re-activates', () => {
    const prev: PhaseTimes = { 3: { startedAt: 1000, endedAt: 5000 } };
    const next = nextPhaseTimes(prev, 3, 'active', 9000);
    expect(next[3]).toEqual({ startedAt: 9000, endedAt: null });
  });

  it('is a no-op when completing a phase that never started', () => {
    const prev: PhaseTimes = {};
    const next = nextPhaseTimes(prev, 7, 'complete', 5000);
    expect(next).toBe(prev);
  });

  it('clears timing when a phase resets to pending', () => {
    const prev: PhaseTimes = { 3: { startedAt: 1000, endedAt: 5000 } };
    const next = nextPhaseTimes(prev, 3, 'pending', 9000);
    expect(next[3]).toEqual({ startedAt: null, endedAt: null });
  });

  it('is a no-op for pending on an untimed phase', () => {
    const prev: PhaseTimes = {};
    expect(nextPhaseTimes(prev, 4, 'pending', 100)).toBe(prev);
  });
});

describe('phaseElapsedMs', () => {
  it('returns null when the phase has no timing', () => {
    expect(phaseElapsedMs(undefined, 100)).toBeNull();
    expect(phaseElapsedMs({ startedAt: null, endedAt: null }, 100)).toBeNull();
  });

  it('measures live phases against now', () => {
    expect(phaseElapsedMs({ startedAt: 1000, endedAt: null }, 4000)).toBe(3000);
  });

  it('measures finished phases against their end time', () => {
    expect(phaseElapsedMs({ startedAt: 1000, endedAt: 2500 }, 9999)).toBe(1500);
  });
});

describe('formatDuration', () => {
  it('formats sub-second, seconds, minutes and hours', () => {
    expect(formatDuration(400)).toBe('0.4s');
    expect(formatDuration(12_000)).toBe('12s');
    expect(formatDuration(185_000)).toBe('3m 05s');
    expect(formatDuration(4_320_000)).toBe('1h 12m');
  });

  it('clamps negative durations to zero', () => {
    expect(formatDuration(-5)).toBe('0s');
  });
});
