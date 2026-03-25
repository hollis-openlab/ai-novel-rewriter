import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Apple-style palette from MASTER.md
        'page': '#F5F5F7',
        'card': '#FFFFFF',
        'subtle': '#E8E8ED',
        'primary': '#1D1D1F',
        'secondary': '#6E6E73',
        'tertiary': '#86868B',
        'accent': '#0071E3',
        'accent-hover': '#0077ED',
        'success': '#34C759',
        'warning': '#FF9500',
        'error': '#FF3B30',
        'ai': '#6366F1',
        'border': '#D2D2D7',
        // Scene colors
        'scene-combat': '#FEF2F2',
        'scene-dialogue': '#EFF6FF',
        'scene-psychology': '#F5F3FF',
        'scene-environment': '#F0FDF4',
        'scene-narration': '#F8FAFC',
        'scene-romance': '#FFF1F2',
        'scene-flashback': '#FFFBEB',
        'scene-daily': '#F0F9FF',
        // Dark mode
        dark: {
          'page': '#000000',
          'card': '#1C1C1E',
          'subtle': '#2C2C2E',
          'primary': '#F5F5F7',
          'secondary': '#A1A1A6',
          'accent': '#0A84FF',
          'border': '#38383A',
        }
      },
      fontFamily: {
        sans: ['Inter', 'PingFang SC', 'Noto Sans SC', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'Fira Code', 'monospace'],
      },
      fontSize: {
        // Apple typography scale from MASTER.md
        'display': ['2.125rem', { lineHeight: '1.12', letterSpacing: '-0.01em', fontWeight: '700' }],
        'title-1': ['1.75rem', { lineHeight: '1.14', letterSpacing: '-0.01em', fontWeight: '700' }],
        'title-2': ['1.375rem', { lineHeight: '1.18', letterSpacing: '-0.005em', fontWeight: '600' }],
        'title-3': ['1.0625rem', { lineHeight: '1.24', letterSpacing: '0', fontWeight: '600' }],
        'body': ['0.9375rem', { lineHeight: '1.53', letterSpacing: '0', fontWeight: '400' }],
        'body-bold': ['0.9375rem', { lineHeight: '1.53', letterSpacing: '0', fontWeight: '600' }],
        'callout': ['0.875rem', { lineHeight: '1.43', letterSpacing: '0', fontWeight: '400' }],
        'caption': ['0.75rem', { lineHeight: '1.33', letterSpacing: '0.01em', fontWeight: '500' }],
        'mono': ['0.8125rem', { lineHeight: '1.5', letterSpacing: '0', fontWeight: '400' }],
      },
      spacing: {
        // Spacing tokens from MASTER.md
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '5': '20px',
        '6': '24px',
        '8': '32px',
        '12': '48px',
      },
      borderRadius: {
        'sm': '8px',
        'md': '12px',
        'lg': '16px',
        'xl': '20px',
        '2xl': '24px',
        'full': '9999px',
      },
      boxShadow: {
        'xs': '0 1px 2px rgba(0,0,0,0.04)',
        'sm': '0 2px 8px rgba(0,0,0,0.06)',
        'md': '0 4px 16px rgba(0,0,0,0.08)',
        'lg': '0 8px 32px rgba(0,0,0,0.12)',
        'ai-glow': '0 0 24px rgba(99,102,241,0.15)',
      },
      transitionDuration: {
        '150': '150ms',
        '200': '200ms',
        '300': '300ms',
      },
      transitionTimingFunction: {
        'ease-out': 'cubic-bezier(0, 0, 0.2, 1)',
      },
      width: {
        'sidebar': '260px',
        'sidebar-collapsed': '64px',
      },
      maxWidth: {
        'content': '1280px',
      },
    }
  },
  plugins: []
} satisfies Config