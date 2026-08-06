import { useCallback, useEffect, useState } from 'react';
import { applyTheme, otherTheme, resolveInitialTheme, THEME_STORAGE_KEY, type Theme } from '@/lib/theme';

/** Theme state with persistence; stamps `data-theme` on <html>. */
export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  const [theme, setTheme] = useState<Theme>(() =>
    resolveInitialTheme(
      localStorage.getItem(THEME_STORAGE_KEY),
      window.matchMedia('(prefers-color-scheme: light)').matches,
    ),
  );

  useEffect(() => {
    applyTheme(theme, document.documentElement);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = otherTheme(prev);
      localStorage.setItem(THEME_STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
