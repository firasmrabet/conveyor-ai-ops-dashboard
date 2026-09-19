'use client';

import {
  Activity, AlertTriangle, BellRing, BrainCircuit, Cpu, Gauge, LayoutDashboard, LineChart,
  Menu, Moon, PlayCircle, Settings, Sun, Wifi, WifiOff,
} from 'lucide-react';
import { useTheme } from 'next-themes';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import * as React from 'react';
import { motion } from 'framer-motion';
import { cn, timeFr } from '@/lib/format';
import { useClock, useCriticalPulse } from '@/lib/hooks';
import { useLive } from '@/lib/store';

const NAV = [
  { href: '/', label: 'Control Room', icon: LayoutDashboard },
  { href: '/analytics', label: 'Analytique', icon: LineChart },
  { href: '/alerts', label: 'Alertes', icon: BellRing },
  { href: '/ml', label: 'Modèles IA', icon: BrainCircuit },
  { href: '/devices', label: 'Équipements', icon: Cpu },
  { href: '/simulator', label: 'Simulateur', icon: PlayCircle },
  { href: '/settings', label: 'Paramètres', icon: Settings },
];

function LiveIndicator() {
  const connection = useLive((s) => s.connection);
  const latency = useLive((s) => s.latencyMs);
  const rates = useLive((s) => s.rates);
  const isLive = connection === 'live';

  return (
    <div className="flex items-center gap-2.5">
      <span
        className={cn(
          'relative inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-wider',
          isLive
            ? 'border-[rgba(0,227,150,0.4)] bg-[rgba(0,227,150,0.12)] text-[#00E396]'
            : connection === 'reconnecting'
              ? 'border-[rgba(255,176,32,0.4)] bg-[rgba(255,176,32,0.12)] text-[#FFB020]'
              : 'border-[rgba(255,77,79,0.4)] bg-[rgba(255,77,79,0.12)] text-[#FF4D4F]',
        )}
        aria-live="polite"
      >
        {isLive && (
          <span className="absolute inset-0 -z-10 animate-pulse-ring rounded-full border border-[rgba(0,227,150,0.5)]" />
        )}
        {isLive ? <Wifi size={12} /> : <WifiOff size={12} />}
        {isLive ? 'LIVE' : connection === 'reconnecting' ? 'RECONNEXION' : 'OFFLINE'}
      </span>
      <span className="num hidden text-[10.5px] text-[rgb(var(--muted))] sm:inline">
        {latency ? `${latency} ms` : '—'} · {rates.telemetry_per_s ?? 0} msg/s · {rates.throughput_kb_s ?? 0} ko/s
      </span>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const clock = useClock();
  const pulse = useCriticalPulse();
  const alerts = useLive((s) => s.alerts);
  const criticalCount = React.useMemo(
    () => alerts.filter((a) => a.severity === 'CRITICAL' && a.status === 'NEW').length,
    [alerts],
  );

  React.useEffect(() => setMounted(true), []);

  return (
    <div className="flex min-h-screen">
      {/* -------- Barre latérale -------- */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 w-[228px] shrink-0 border-r border-white/[0.06] bg-[rgb(var(--bg-soft)/0.92)] backdrop-blur-xl transition-transform lg:static lg:translate-x-0',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center gap-2.5 px-4 py-4">
            <div className="grid h-9 w-9 place-items-center rounded-xl border border-[rgba(0,229,255,0.35)] bg-[rgba(0,229,255,0.12)]">
              <Gauge size={18} className="text-[#00E5FF]" />
            </div>
            <div>
              <p className="font-display text-[13px] font-semibold leading-tight">Conveyor AI Ops</p>
              <p className="text-[10px] text-[rgb(var(--muted))]">Supervision prédictive</p>
            </div>
          </div>

          <nav className="mt-1 flex-1 space-y-0.5 px-2" aria-label="Navigation principale">
            {NAV.map((item) => {
              const active = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileOpen(false)}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'group relative flex items-center gap-2.5 rounded-xl px-3 py-2 text-[12.5px] transition-all',
                    active
                      ? 'bg-[rgba(0,229,255,0.10)] text-[#7CF2FF]'
                      : 'text-[rgb(var(--muted))] hover:bg-white/[0.05] hover:text-[rgb(var(--text))]',
                  )}
                >
                  {active && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute left-0 top-1.5 h-[calc(100%-12px)] w-[2px] rounded-full bg-[#00E5FF]"
                    />
                  )}
                  <Icon size={15} />
                  {item.label}
                  {item.href === '/alerts' && criticalCount > 0 && (
                    <span className="num ml-auto rounded-full bg-[rgba(255,77,79,0.2)] px-1.5 py-0.5 text-[9.5px] font-semibold text-[#FF4D4F]">
                      {criticalCount}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-white/[0.06] p-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5">
              <p className="label mb-1">Pipeline IA</p>
              <p className="text-[10.5px] leading-relaxed text-[rgb(var(--muted))]">
                ESP32 → MQTT → features → Isolation&nbsp;Forest / RandomForest → RUL → Health Index
              </p>
            </div>
            <div className="mt-2.5 flex items-center gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
              <div className="grid h-7 w-7 place-items-center rounded-full bg-[rgba(124,92,255,0.2)] text-[10.5px] font-semibold text-[#C9BBFF]">
                AB
              </div>
              <div className="min-w-0">
                <p className="truncate text-[11.5px] font-medium">Firas Mrabet</p>
                <p className="text-[10px] text-[rgb(var(--muted))]">Administrateur</p>
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* -------- Contenu -------- */}
      <div className={cn('flex min-w-0 flex-1 flex-col', pulse && 'animate-shake')}>
        <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-[rgb(var(--bg)/0.82)] backdrop-blur-xl">
          <div className="flex items-center gap-3 px-4 py-2.5">
            <button
              type="button"
              className="rounded-lg border border-white/10 p-1.5 lg:hidden"
              onClick={() => setMobileOpen((open) => !open)}
              aria-label="Ouvrir le menu"
            >
              <Menu size={16} />
            </button>

            <div className="hidden items-center gap-2 text-[11.5px] text-[rgb(var(--muted))] md:flex">
              <Activity size={13} className="text-[#00E396]" />
              {/* Gabarits stables avant montage : le HTML pré-rendu et le premier rendu
                  client doivent être identiques, sinon l'hydratation échoue. */}
              <span className="num">{clock ? timeFr(clock) : '--:--:--'}</span>
              <span className="text-white/15">|</span>
              <span>
                {clock
                  ? clock.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })
                  : '\u00a0'}
              </span>
            </div>

            <div className="ml-auto flex items-center gap-3">
              <LiveIndicator />
              <button
                type="button"
                onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                className="rounded-lg border border-white/10 p-1.5 transition hover:bg-white/[0.06]"
                aria-label={theme === 'dark' ? 'Passer en thème clair' : 'Passer en thème sombre'}
              >
                {mounted && theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
              </button>
              {criticalCount > 0 && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(255,77,79,0.35)] bg-[rgba(255,77,79,0.12)] px-2.5 py-1 text-[10.5px] font-semibold text-[#FF4D4F]">
                  <AlertTriangle size={12} />
                  {criticalCount} CRITIQUE{criticalCount > 1 ? 'S' : ''}
                </span>
              )}
            </div>
          </div>
        </header>

        <main className="min-w-0 flex-1 p-3 sm:p-4">{children}</main>

        <footer className="border-t border-white/[0.06] px-4 py-2.5 text-[10.5px] text-[rgb(var(--muted))]">
          Projet IoT + Edge AI — Détection d'anomalies et maintenance prédictive d'un tapis roulant industriel ·
          FastAPI · Next.js · MQTT · WebSocket
        </footer>
      </div>

      {mobileOpen && (
        <button
          type="button"
          aria-label="Fermer le menu"
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}
    </div>
  );
}
