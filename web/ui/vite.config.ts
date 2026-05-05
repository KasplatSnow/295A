import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import runtimeErrorOverlay from "@replit/vite-plugin-runtime-error-modal";

export default defineConfig({
  plugins: [
    react(),
    runtimeErrorOverlay(),
    ...(process.env.NODE_ENV !== "production" &&
    process.env.REPL_ID !== undefined
      ? [
          await import("@replit/vite-plugin-cartographer").then((m) =>
            m.cartographer(),
          ),
          await import("@replit/vite-plugin-dev-banner").then((m) =>
            m.devBanner(),
          ),
        ]
      : []),
  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "client", "src"),
      "@shared": path.resolve(import.meta.dirname, "shared"),
      "@assets": path.resolve(import.meta.dirname, "attached_assets"),
    },
  },
  root: path.resolve(import.meta.dirname, "client"),
  envDir: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist/public"),
    emptyOutDir: true,
  },
  server: {
    fs: {
      strict: true,
      deny: ["**/.*"],
    },
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
      "/mediamtx_api": {
        target: process.env.VITE_MEDIAMTX_API_TARGET || "http://127.0.0.1:9997",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/mediamtx_api/, ''),
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            // Suppress the ECONNREFUSED logs since the frontend handles the healthcheck fallback gracefully.
            if (err.message.includes('ECONNREFUSED')) return;
            console.error('proxy error', err);
          });
        }
      },
    },
  },
});
