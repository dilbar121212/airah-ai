import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server on port 5000 (Replit's webview port). /api is proxied to the
// FastAPI backend on localhost:8000, so the browser always talks to the
// same origin — no localhost URL is ever hardcoded in the frontend.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5000,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 5000,
  },
});