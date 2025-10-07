import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import process from 'node:process'

// Ignore broken pipe errors in dev when output is piped or a client aborts.
try {
  // Only swallow EPIPE; surface all other errors.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const onIoError = (err: any) => {
    if (!err || err.code === 'EPIPE') return
    throw err
  }
  // @ts-ignore - process types may not be available in this context
  process.stdout?.on?.('error', onIoError)
  // @ts-ignore - process types may not be available in this context
  process.stderr?.on?.('error', onIoError)
} catch {}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:5050',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // Quiet transient proxy errors from client/server aborts
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const code = (err as any)?.code
            if (code !== 'ECONNRESET' && code !== 'EPIPE') {
              // eslint-disable-next-line no-console
              console.error('[vite proxy /api] error:', code, err?.message)
            }
          })
        },
      },
      '/ws': {
        target: process.env.VITE_API_URL || 'http://localhost:5050',
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path,
        // Quiet transient proxy errors from client/server aborts
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const code = (err as any)?.code
            if (code !== 'ECONNRESET' && code !== 'EPIPE') {
              // eslint-disable-next-line no-console
              console.error('[vite proxy /ws] error:', code, err?.message)
            }
          })
        },
      },
    },
  },
})
