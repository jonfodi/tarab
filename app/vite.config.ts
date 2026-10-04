import { defineConfig } from "vite";

// Tauri expects a fixed port and doesn't want the screen cleared.
export default defineConfig({
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "safari15", outDir: "dist" },
});
