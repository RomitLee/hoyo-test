import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: '../server/app/web-admin', emptyOutDir: true },
  server: { proxy: { '/api': process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' } },
})
