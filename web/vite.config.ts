import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

// Explicitly IPv4. `localhost` resolves to ::1 first on Windows, so a machine
// with anything else bound to that port on IPv6 silently proxies there instead
// — which looks like the backend behaving strangely rather than like a request
// reaching a different program entirely.
const backend = process.env.VITE_PROXY_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  base: process.env.VITE_CDN_BASE ?? '/',

  build: {
    // Named chunks rather than automatic splitting. The markdown and chart
    // stacks are large and only some routes need them; leaving it to the
    // bundler puts them in whichever chunk happened to import them first.
    rollupOptions: {
      input: 'index.html',
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-query': ['@tanstack/react-query'],
        },
      },
    },
  },

  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api/v1': {
        target: backend,
        changeOrigin: true,
      },
      '/ws/v1': {
        target: backend.replace(/^http/, 'ws'),
        ws: true,
        changeOrigin: true,
      },
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
} as Parameters<typeof defineConfig>[0])
