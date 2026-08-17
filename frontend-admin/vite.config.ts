import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  base: '/admin/',
  plugins: [vue()],
  build: {
    outDir: '../app/static/admin',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
  },
});
