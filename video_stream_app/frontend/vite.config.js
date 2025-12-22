import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,  // Changed to avoid conflict
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        ws: true  // Enable WebSocket proxy
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets'
  }
})

