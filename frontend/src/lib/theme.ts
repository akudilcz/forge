/**
 * Theme resolution — pure logic behind hooks/useTheme.ts.
 *
 * The app defaults to the dark control-station theme. A stored preference
 * wins; otherwise the OS `prefers-color-scheme` hint decides.
 */

export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'forge.theme';

/** Resolve the initial theme from a stored value and the OS hint. */
export function resolveInitialTheme(stored: string | null, prefersLight: boolean): Theme {
  if (stored === 'dark' || stored === 'light') return stored;
  return prefersLight ? 'light' : 'dark';
}

/** Stamp the theme on the document root so CSS variables switch. */
export function applyTheme(theme: Theme, root: { setAttribute(k: string, v: string): void }): void {
  root.setAttribute('data-theme', theme);
}

export function otherTheme(theme: Theme): Theme {
  return theme === 'dark' ? 'light' : 'dark';
}
