import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * Everything the backend serves is proxied in development — not just ``/api``.
 *
 * The MCP surface does not live under ``/api``: the connector endpoints are at
 * ``/connectors/{client}/mcp``, the shared OAuth endpoints are at ``/oauth/*``,
 * discovery documents are at ``/.well-known/…`` and the generic endpoint is at
 * ``/mcp``. Without those entries the preview would 404 the very requests the
 * AI-connector screen and the hosted clients make, which looks exactly like
 * "the MCP is broken".
 *
 * ``xfwd: true`` is what makes the backend's public-URL detection work through
 * the proxy: it forwards X-Forwarded-Host/Proto, so the OAuth metadata names the
 * host the browser actually used instead of ``0.0.0.0:8000``.
 */
const API_TARGET = process.env.VITE_API_TARGET || "http://0.0.0.0:8000";

const proxy = Object.fromEntries(
  ["/api", "/mcp", "/oauth", "/connectors", "/.well-known", "/health"].map((context) => [
    context,
    { target: API_TARGET, changeOrigin: true, xfwd: true, secure: false },
  ])
);

export default defineConfig({
  plugins: [react()],
  root: "frontend",
  build: {
    outDir: "dist",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "frontend/src"),
    },
  },
  server: {
    port: 5173,
    host: true,
    // Allow proxied preview/tunnel hostnames (e.g. *.e2b.app) in addition to
    // localhost, so the dev server is reachable from a remote browser.
    allowedHosts: true,
    proxy,
  },
  preview: {
    port: 4173,
    host: true,
    allowedHosts: true,
    proxy,
  },
});
