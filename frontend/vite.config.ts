import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the browser talks only to Vite; /api/* is forwarded to the FastAPI backend,
// so no cross-origin requests happen. A deployed build sets VITE_API_BASE instead.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.PROPLOCK_API ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
