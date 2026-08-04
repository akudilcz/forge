/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:7340',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '/api/v1'),
      },
      '/auth': {
        target: 'http://localhost:7340',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:7340',
        ws: true,
        changeOrigin: true,
        configure: (proxy) => {
          // Gracefully handle transient WebSocket proxy errors (EPIPE / ECONNRESET)
          // that surface when the backend is temporarily unavailable or the browser
          // closes its tab before the server finishes flushing. Without this handler
          // Node's default behaviour re-throws the error and prints the full stack
          // trace to the console on every disconnect.
          proxy.on('error', (err: Error & { code?: string }) => {
            console.warn('[vite ws proxy]', err.code ?? err.message);
          });
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
