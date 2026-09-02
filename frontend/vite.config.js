import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Build output lands inside static/app/ (committed to git — see the plan's
// D3) so academy.spec's existing datas=[("static","static")] and the /static
// mount need no changes, and reception.html / scanner-test.html / style.css
// keep their exact current URLs untouched.
export default defineConfig({
  plugins: [react()],
  base: '/static/app/',
  build: {
    outDir: '../static/app',
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` runs on :5173 against a real backend on :8000 (started
    // separately with ./start.sh) — everything that isn't a Vite asset goes
    // there, so the dev server behaves like the production one-file app.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/photos': 'http://127.0.0.1:8000',
      '/cards': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
      '/reception': 'http://127.0.0.1:8000',
    },
  },
})
