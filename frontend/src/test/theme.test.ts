import { describe, it, expect } from 'vitest';
import { resolveInitialTheme, applyTheme, otherTheme } from '@/lib/theme';

describe('resolveInitialTheme', () => {
  it('honours a stored preference over the OS hint', () => {
    expect(resolveInitialTheme('light', false)).toBe('light');
    expect(resolveInitialTheme('dark', true)).toBe('dark');
  });

  it('falls back to the OS hint when nothing valid is stored', () => {
    expect(resolveInitialTheme(null, true)).toBe('light');
    expect(resolveInitialTheme(null, false)).toBe('dark');
    expect(resolveInitialTheme('garbage', true)).toBe('light');
  });
});

describe('applyTheme', () => {
  it('stamps data-theme on the given root', () => {
    const calls: Array<[string, string]> = [];
    applyTheme('light', { setAttribute: (k, v) => calls.push([k, v]) });
    expect(calls).toEqual([['data-theme', 'light']]);
  });
});

describe('otherTheme', () => {
  it('flips between dark and light', () => {
    expect(otherTheme('dark')).toBe('light');
    expect(otherTheme('light')).toBe('dark');
  });
});
