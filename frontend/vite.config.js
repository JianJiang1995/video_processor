import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  
  // 解析配置
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  
  // 开发服务器配置
  server: {
    port: 5133,
    strictPort: false,  // 允许自动切换端口，但会打印警告
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        ws: true,
        timeout: 60000  // 增加代理超时到 60 秒
      },
      '/sessions': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        timeout: 60000
      }
    }
  },
  
  // 构建配置
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // Electron 需要相对路径
    base: './',
    // 确保资源使用相对路径
    rollupOptions: {
      output: {
        // 确保入口文件使用相对路径
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]'
      }
    }
  },
  
  // 基础路径 - 生产环境使用相对路径以支持 Electron
  base: process.env.NODE_ENV === 'production' ? './' : '/'
})
