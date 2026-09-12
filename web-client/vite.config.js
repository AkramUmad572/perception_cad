import { defineConfig } from "vite";
import basicSsl from "@vitejs/plugin-basic-ssl";

// Default: HTTP so Cursor/Chrome on localhost works (localhost is a secure context for WebXR).
// Quest on LAN needs HTTPS: npm run dev:https → open https://YOUR_LAN_IP:5173 and accept the cert.
const useHttps = process.env.CAD_HTTPS === "1";

const proxyOpts = {
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  secure: false,
  timeout: 120000,
  proxyTimeout: 120000,
};

export default defineConfig({
  plugins: useHttps ? [basicSsl()] : [],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": proxyOpts,
      "/media": proxyOpts,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
