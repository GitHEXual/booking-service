import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // В разработке интерфейс и сервис живут на разных портах. Прокси отправляет
    // запросы к сервису и, что важнее, отдаёт куку сессии тому же имени хоста,
    // откуда её выдал: иначе запросы шли бы без куки и панель считала бы, что
    // никто не вошёл.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/auth": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
