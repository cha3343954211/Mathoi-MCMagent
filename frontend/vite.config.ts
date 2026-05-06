import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // HTTP REST + WebSocket 统一走同一规则
      // ws:true 使 Vite 同时代理 WebSocket Upgrade（/api/ws/tasks/:id）
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    }
  }
})
