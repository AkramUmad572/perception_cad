import { defineConfig } from "vite";
import { iwsdkDev } from "@iwsdk/vite-plugin-dev";

/**
 * IWSDK-style desktop testing (Meta recommended WebXR path):
 * - HTTPS for Quest + secure context
 * - IWER Quest 3 emulator on localhost only (not on LAN headset IP)
 * - Managed Playwright window in collaborate mode (like hologram / iwsdk dev up)
 *
 * Docs: https://developers.meta.com/horizon/documentation/web/webxr-overview/
 */
// Longer than the client's own abort, so a slow build fails with a spoken
// message instead of the proxy silently cutting the connection first.
const proxyOpts = {
  target: "http://127.0.0.1:8000",
  changeOrigin: true,
  secure: false,
  timeout: 300000,
  proxyTimeout: 300000,
};

export default defineConfig({
  plugins: [
    iwsdkDev({
      emulator: {
        device: "metaQuest3",
        environment: "living_room",
        activation: "localhost",
        userAgentException: /OculusBrowser/,
      },
      ai: {
        mode: "collaborate",
      },
      https: true,
      verbose: true,
    }),
  ],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": proxyOpts,
      "/media": proxyOpts,
    },
  },
  resolve: {
    // Let @iwsdk use its own three alias; app imports stay on package three.
  },
  optimizeDeps: {
    exclude: ["@iwsdk/core"],
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
