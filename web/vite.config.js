import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the API and card images to a running crm.py; `npm run build` writes web/dist, which crm.py serves.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8770", "/cards": "http://127.0.0.1:8770" } },
  // One bundle: OpenUI lazy-loads every lucide icon as its own chunk (~1,600 files), too many to commit.
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 5000, rollupOptions: { output: { inlineDynamicImports: true } } },
});
