import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['class'],
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Palette industrielle du design system (cf. README § Design)
        base: { DEFAULT: '#0A0E1A', soft: '#0F1420', panel: '#161B2B', line: 'rgba(255,255,255,0.06)' },
        accent: {
          cyan: '#00E5FF', green: '#00E396', amber: '#FFB020',
          red: '#FF4D4F', violet: '#7C5CFF', blue: '#4D9FFF',
        },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'Inter', 'system-ui', 'sans-serif'],
        display: ['var(--font-space)', 'Space Grotesk', 'Inter', 'sans-serif'],
        mono: ['var(--font-mono)', 'JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: { xl: '12px', '2xl': '16px', '3xl': '22px' },
      boxShadow: {
        glow: '0 0 24px -6px rgba(0,229,255,0.45)',
        'glow-red': '0 0 26px -6px rgba(255,77,79,0.5)',
        'glow-violet': '0 0 24px -6px rgba(124,92,255,0.45)',
        panel: '0 10px 30px -18px rgba(0,0,0,0.9)',
      },
      backgroundImage: {
        'grid-fade': 'radial-gradient(circle at 20% 0%, rgba(0,229,255,0.06), transparent 45%), radial-gradient(circle at 100% 0%, rgba(124,92,255,0.07), transparent 40%)',
      },
      keyframes: {
        'pulse-ring': { '0%': { transform: 'scale(0.8)', opacity: '0.7' }, '100%': { transform: 'scale(2.2)', opacity: '0' } },
        shake: { '0%,100%': { transform: 'translateX(0)' }, '20%,60%': { transform: 'translateX(-3px)' }, '40%,80%': { transform: 'translateX(3px)' } },
        'slide-in': { from: { opacity: '0', transform: 'translateY(-6px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'pulse-ring': 'pulse-ring 1.8s cubic-bezier(0.4,0,0.6,1) infinite',
        shake: 'shake 0.4s ease-in-out',
        'slide-in': 'slide-in 0.28s ease-out',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
};
export default config;
