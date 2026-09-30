import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  plugins: [vue()],
  build: {
    outDir: fileURLToPath(new URL('../python/solix_link/web', import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/devices': 'http://127.0.0.1:8765',
    },
  },
});
