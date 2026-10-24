import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { clientConfig } from "./src/runtimeConfig.ts";

export default defineConfig(({ mode }) => {
  if (mode === "ios") {
    const env = loadEnv(mode, process.cwd());
    clientConfig({
      VITE_API_BASE_URL: env.VITE_API_BASE_URL,
      VITE_PUBLIC_APP_URL: env.VITE_PUBLIC_APP_URL,
    }, true);
  }
  return {
  plugins: [react()],
  build: { outDir: mode === "ios" ? "dist-ios" : "dist" },
  server: {
    port: 5173,
    allowedHosts: ["rental-mgmt.us"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  };
});
