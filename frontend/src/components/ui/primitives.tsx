'use client';

/** Primitives UI « shadcn-compatibles » écrites à la main (aucune dépendance externe). */
import { motion } from 'framer-motion';
import * as React from 'react';
import { cn } from '@/lib/format';

// ------------------------------------------------------------------ Panel
export function Panel({
  children, className, padded = true, hover = false, ...props
}: React.HTMLAttributes<HTMLDivElement> & { padded?: boolean; hover?: boolean }) {
  return (
    <div className={cn('panel', padded && 'p-4', hover && 'panel-hover', className)} {...props}>
      {children}
    </div>
  );
}

export function PanelHeader({
  title, subtitle, icon, right, className,
}: { title: React.ReactNode; subtitle?: React.ReactNode; icon?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn('mb-3 flex items-start justify-between gap-3', className)}>
      <div className="flex items-start gap-2.5">
        {icon && <span className="mt-0.5 text-[rgb(var(--cyan))]">{icon}</span>}
        <div>
          <h3 className="font-display text-[13px] font-semibold tracking-wide">{title}</h3>
          {subtitle && <p className="mt-0.5 text-[11px] text-[rgb(var(--muted))]">{subtitle}</p>}
        </div>
      </div>
      {right}
    </div>
  );
}

// ------------------------------------------------------------------ Badge
const BADGE_TONES = {
  neutral: 'bg-white/5 text-[rgb(var(--muted))] border-white/10',
  cyan: 'bg-[rgba(0,229,255,0.12)] text-[#00E5FF] border-[rgba(0,229,255,0.32)]',
  green: 'bg-[rgba(0,227,150,0.12)] text-[#00E396] border-[rgba(0,227,150,0.32)]',
  amber: 'bg-[rgba(255,176,32,0.12)] text-[#FFB020] border-[rgba(255,176,32,0.32)]',
  red: 'bg-[rgba(255,77,79,0.14)] text-[#FF4D4F] border-[rgba(255,77,79,0.34)]',
  violet: 'bg-[rgba(124,92,255,0.14)] text-[#B9A6FF] border-[rgba(124,92,255,0.34)]',
} as const;

export function Badge({
  children, tone = 'neutral', className, dot = false,
}: { children: React.ReactNode; tone?: keyof typeof BADGE_TONES; className?: string; dot?: boolean }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full border px-2 py-[3px] text-[10.5px] font-medium uppercase tracking-wide', BADGE_TONES[tone], className)}>
      {dot && <span className={cn('h-1.5 w-1.5 rounded-full', tone === 'green' && 'bg-[#00E396]', tone === 'red' && 'bg-[#FF4D4F]', tone === 'amber' && 'bg-[#FFB020]', tone === 'cyan' && 'bg-[#00E5FF]', tone === 'violet' && 'bg-[#B9A6FF]', tone === 'neutral' && 'bg-white/40')} />}
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ Button
export function Button({
  children, onClick, variant = 'default', size = 'md', className, disabled, type = 'button', title, ariaLabel, active,
}: {
  children: React.ReactNode; onClick?: () => void; variant?: 'default' | 'primary' | 'ghost' | 'danger' | 'success' | 'violet';
  size?: 'sm' | 'md' | 'lg'; className?: string; disabled?: boolean; type?: 'button' | 'submit';
  title?: string; ariaLabel?: string; active?: boolean;
}) {
  const variants = {
    default: 'bg-white/[0.06] hover:bg-white/[0.12] border-white/10',
    primary: 'bg-[rgba(0,229,255,0.14)] hover:bg-[rgba(0,229,255,0.24)] border-[rgba(0,229,255,0.4)] text-[#7CF2FF]',
    success: 'bg-[rgba(0,227,150,0.14)] hover:bg-[rgba(0,227,150,0.24)] border-[rgba(0,227,150,0.4)] text-[#5BF3BE]',
    danger: 'bg-[rgba(255,77,79,0.15)] hover:bg-[rgba(255,77,79,0.26)] border-[rgba(255,77,79,0.42)] text-[#FF9294]',
    violet: 'bg-[rgba(124,92,255,0.16)] hover:bg-[rgba(124,92,255,0.28)] border-[rgba(124,92,255,0.42)] text-[#C9BBFF]',
    ghost: 'bg-transparent hover:bg-white/[0.06] border-transparent',
  } as const;
  const sizes = { sm: 'px-2.5 py-1 text-[11px]', md: 'px-3 py-1.5 text-[12px]', lg: 'px-4 py-2 text-[13px]' } as const;

  return (
    <motion.button
      type={type}
      whileTap={{ scale: disabled ? 1 : 0.97 }}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      aria-pressed={active}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-xl border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45',
        variants[variant], sizes[size], active && 'ring-1 ring-[rgba(0,229,255,0.5)]', className,
      )}
    >
      {children}
    </motion.button>
  );
}

// ------------------------------------------------------------------ Toggle segmenté
export function Segmented<T extends string>({
  options, value, onChange, size = 'md', ariaLabel,
}: { options: { value: T; label: string; icon?: React.ReactNode; title?: string }[]; value: T; onChange: (value: T) => void; size?: 'sm' | 'md'; ariaLabel?: string }) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="inline-flex rounded-xl border border-white/10 bg-white/[0.03] p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={value === option.value}
          title={option.title}
          onClick={() => onChange(option.value)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-[10px] font-medium transition-all',
            size === 'sm' ? 'px-2 py-1 text-[11px]' : 'px-3 py-1.5 text-[12px]',
            value === option.value
              ? 'bg-[rgba(0,229,255,0.14)] text-[#7CF2FF] shadow-[inset_0_0_0_1px_rgba(0,229,255,0.28)]'
              : 'text-[rgb(var(--muted))] hover:text-[rgb(var(--text))]',
          )}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Stat / KPI
export function Stat({
  label, value, unit, hint, tone = 'neutral', icon, className,
}: {
  label: string; value: React.ReactNode; unit?: string; hint?: React.ReactNode;
  tone?: 'neutral' | 'cyan' | 'green' | 'amber' | 'red' | 'violet'; icon?: React.ReactNode; className?: string;
}) {
  const glow = {
    neutral: '', cyan: 'shadow-glow', green: 'shadow-[0_0_22px_-8px_rgba(0,227,150,0.7)]',
    amber: 'shadow-[0_0_22px_-8px_rgba(255,176,32,0.7)]', red: 'shadow-glow-red', violet: 'shadow-glow-violet',
  }[tone];
  const color = {
    neutral: 'rgb(var(--text))', cyan: '#00E5FF', green: '#00E396', amber: '#FFB020', red: '#FF4D4F', violet: '#B9A6FF',
  }[tone];

  return (
    <div className={cn('panel panel-hover px-3.5 py-3', glow, className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="label">{label}</span>
        {icon && <span className="opacity-70" style={{ color }}>{icon}</span>}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className="num text-2xl font-semibold leading-none" style={{ color }}>{value}</span>
        {unit && <span className="text-[11px] text-[rgb(var(--muted))]">{unit}</span>}
      </div>
      {hint && <div className="mt-1 text-[10.5px] text-[rgb(var(--muted))]">{hint}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Skeleton
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} />;
}

// ------------------------------------------------------------------ Tooltip simple
export function Tip({ children, content }: { children: React.ReactNode; content: React.ReactNode }) {
  return (
    <span className="group relative inline-flex">
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 hidden -translate-x-1/2 whitespace-nowrap rounded-lg border border-white/10 bg-[rgb(var(--bg-soft))] px-2 py-1 text-[11px] text-[rgb(var(--text))] shadow-panel group-hover:block">
        {content}
      </span>
    </span>
  );
}

// ------------------------------------------------------------------ Progress bar
export function ProgressBar({ value, tone = 'cyan', height = 6, label }: { value: number; tone?: 'cyan' | 'green' | 'amber' | 'red' | 'violet'; height?: number; label?: string }) {
  const colors = { cyan: '#00E5FF', green: '#00E396', amber: '#FFB020', red: '#FF4D4F', violet: '#7C5CFF' } as const;
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className="w-full" aria-label={label} role="progressbar" aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100}>
      <div className="w-full overflow-hidden rounded-full bg-white/[0.07]" style={{ height }}>
        <motion.div
          className="h-full rounded-full"
          style={{ background: `linear-gradient(90deg, ${colors[tone]}55, ${colors[tone]})` }}
          initial={{ width: 0 }}
          animate={{ width: `${clamped}%` }}
          transition={{ type: 'spring', stiffness: 120, damping: 20 }}
        />
      </div>
    </div>
  );
}

export function EmptyState({ title, description, icon }: { title: string; description?: string; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      {icon && <span className="text-[rgb(var(--muted))]">{icon}</span>}
      <p className="text-[13px] font-medium">{title}</p>
      {description && <p className="max-w-sm text-[11.5px] text-[rgb(var(--muted))]">{description}</p>}
    </div>
  );
}

/** Interrupteur accessible (clavier + souris) utilisé par les pages Réglages/Alertes. */
export function Toggle({
  checked, onChange, label, description,
}: { checked: boolean; onChange: (next: boolean) => void; label: string; description?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="flex w-full items-start justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5 text-left transition hover:border-white/[0.12]"
    >
      <span>
        <span className="block text-[11.5px] font-medium">{label}</span>
        {description && <span className="mt-0.5 block text-[10px] text-[rgb(var(--muted))]">{description}</span>}
      </span>
      <span
        className={`relative mt-0.5 h-[18px] w-[32px] shrink-0 rounded-full transition-colors ${checked ? 'bg-[rgba(0,229,255,0.85)]' : 'bg-white/15'}`}
      >
        <span
          className={`absolute top-[2px] h-[14px] w-[14px] rounded-full bg-[#0A0E1A] transition-all ${checked ? 'left-[16px]' : 'left-[2px]'}`}
        />
      </span>
    </button>
  );
}
