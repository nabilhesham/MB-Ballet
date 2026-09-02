import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Build output lands inside static/app/ (committed to git — see the plan's
// D3) so academy.spec's existing datas=[("static","static")] and the /static
// mount need no changes, and reception.html / scanner-test.html / style.css
// keep their exact current URLs untouched.
export default defineConfig({
  plugins: [
    react(),
    {
      // Vite's dev server prefixes every root-absolute href/src it finds in
      // index.html with `base` — correct for this app's own bundled assets,
      // wrong for style.css and the favicons, which deliberately live
      // outside static/app/ at a fixed URL shared with reception.html and
      // scanner-test.html (see the comment below and CLAUDE.md). Left alone,
      // `/static/style.css` becomes `/static/app/static/style.css` in dev,
      // which doesn't exist, and the page loads unstyled. `order: 'post'`
      // runs this after Vite's own rewrite, undoing it for just these.
      name: 'fix-shared-static-hrefs',
      apply: 'serve',
      transformIndexHtml: {
        order: 'post',
        handler: html => html.replace(/\/static\/app\/static\//g, '/static/'),
      },
    },
  ],
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
      '/reception': 'http://127.0.0.1:8000',
      '/static': {
        target: 'http://127.0.0.1:8000',
        bypass(req) {
          // /static/app is this app's own `base` above — in dev, Vite serves
          // its live index.html, the HMR client and the module graph from
          // frontend/src there itself; none of that exists as real files
          // until `npm run build` runs. A bare '/static' proxy rule would
          // intercept those requests before Vite's own dev-serving got a
          // chance, forwarding them to the backend instead — which has no
          // route for a bare directory and 404s. Returning the URL
          // unchanged tells Vite to handle this one itself; genuine static
          // files (style.css, reception.html, the logo, ...) still fall
          // through to the backend as before.
          if (req.url.startsWith('/static/app')) return req.url;
        },
      },
    },
  },
})
