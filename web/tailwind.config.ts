import type { Config } from 'tailwindcss'
import typography from '@tailwindcss/typography'

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: '#0b0f19',
        panel: '#0e1424',
        card: '#111827',
        muted: '#1f2937',
        border: '#2a3243',
        primary: { DEFAULT: '#6366f1', foreground: '#eef2ff' },
        accent: { DEFAULT: '#8b5cf6', foreground: '#f5f3ff' },
        success: '#10b981',
        warning: '#f59e0b',
        danger: '#ef4444',
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(99,102,241,.25), 0 10px 30px -10px rgba(99,102,241,.35)',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'fade-in-up': {
          '0%': { opacity: '0', transform: 'translateY(8px) scale(.98)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 400ms ease-out forwards',
        'fade-in-up': 'fade-in-up 500ms cubic-bezier(.16,1,.3,1) forwards',
      },
    },
  },
  plugins: [typography],
} satisfies Config
