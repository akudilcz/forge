import type { Config } from 'tailwindcss';

/**
 * FORGE design tokens.
 *
 * Every `forge-*` colour resolves to a CSS variable declared in index.css,
 * where the dark (default) and light themes each define their own values.
 * Variables hold raw RGB triplets so Tailwind's `/opacity` modifiers work.
 */
function token(name: string): string {
  return `rgb(var(--forge-${name}) / <alpha-value>)`;
}

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        forge: {
          bg: token('bg'),
          surface: token('surface'),
          raised: token('raised'),
          border: token('border'),
          'border-accent': token('border-strong'),
          text: token('text'),
          muted: token('muted'),
          faint: token('faint'),
          accent: token('accent'),
          'accent-dim': token('accent-dim'),
          success: token('success'),
          warning: token('warning'),
          error: token('error'),
          purple: token('purple'),
          orange: token('orange'),
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-in-out',
        'slide-in': 'slideIn 0.2s ease-out',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideIn: {
          from: { transform: 'translateX(-8px)', opacity: '0' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
