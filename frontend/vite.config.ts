import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: true,
    clearMocks: true,
    restoreMocks: true,
    mockReset: true,
    exclude: ['node_modules', 'dist', '../backend/static'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8899',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'ws://localhost:8899',
        ws: true,
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  }
})
