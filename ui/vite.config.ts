import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5420, strictPort: true, host: "localhost" },
  worker: { format: "es" },
  build: { chunkSizeWarningLimit: 4000 },
});
