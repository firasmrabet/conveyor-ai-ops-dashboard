'use client';

/** Flux d'alertes temps réel (timeline animée, sévérité colorée). */
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, ShieldCheck } from 'lucide-react';
import Link from 'next/link';
import { AnimatePresence, motion } from 'framer-motion';
import { Badge, Button, EmptyState, Panel, PanelHeader } from '@/components/ui/primitives';
import { SEVERITY_COLORS, FAULT_LABELS, timeFr } from '@/lib/format';
import { useLive } from '@/lib/store';

export function AlertFeed({ limit = 40, compact = false }: { limit?: number; compact?: boolean }) {
  const alerts = useLive((s) => s.alerts);
  const shown = alerts.slice(0, limit);

  return (
    <Panel className="flex h-full min-h-0 flex-col">
      <PanelHeader
        title="Flux d'alertes"
        subtitle={`${alerts.filter((a) => a.status === 'NEW').length} nouvelle(s) · ${alerts.length} en mémoire`}
        icon={<AlertOctagon size={14} />}
        right={
          <Link href="/alerts">
            <Button size="sm" variant="ghost">Tout voir</Button>
          </Link>
        }
      />
      <div className="scroll-thin -mr-1 max-h-[520px] min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        <AnimatePresence initial={false}>
          {shown.map((alert) => {
            const Icon = alert.severity === 'CRITICAL' ? AlertOctagon : alert.severity === 'WARNING' ? AlertTriangle : Info;
            return (
              <motion.div
                key={alert.id}
                layout
                initial={{ opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.22 }}
                className="relative rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5 pl-3"
              >
                <span className="absolute inset-y-2 left-0 w-[3px] rounded-full" style={{ background: SEVERITY_COLORS[alert.severity] }} />
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2">
                    <Icon size={13} style={{ color: SEVERITY_COLORS[alert.severity] }} className="mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-[11.5px] font-medium leading-snug">{alert.message}</p>
                      <p className="mt-0.5 text-[10px] text-[rgb(var(--muted))]">
                        <span className="num">{timeFr(alert.ts)}</span> · {alert.motor_id} · {alert.type} · origine {alert.origin}
                        {alert.metric ? ` · ${alert.metric} = ${typeof alert.value === 'number' ? alert.value.toFixed(3) : alert.value}` : ''}
                      </p>
                      {!compact && alert.context?.recommendation ? (
                        <p className="mt-1 text-[10.5px] text-[#B9A6FF]">→ {String(alert.context.recommendation)}</p>
                      ) : null}
                    </div>
                  </div>
                  <Badge tone={alert.severity === 'CRITICAL' ? 'red' : alert.severity === 'WARNING' ? 'amber' : 'cyan'}>
                    {alert.severity}
                  </Badge>
                </div>
                {alert.context?.fault_probabilities ? (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(alert.context.fault_probabilities as Record<string, number>)
                      .sort(([, a], [, b]) => b - a)
                      .slice(0, 3)
                      .map(([key, value]) => (
                        <span key={key} className="rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[9.5px] text-[rgb(var(--muted))]">
                          {FAULT_LABELS[key] ?? key} {(value * 100).toFixed(0)} %
                        </span>
                      ))}
                  </div>
                ) : null}
              </motion.div>
            );
          })}
        </AnimatePresence>
        {shown.length === 0 && (
          <EmptyState
            icon={<ShieldCheck size={22} className="text-[#00E396]" />}
            title="Aucune alerte active"
            description="Les deux moteurs évoluent dans leur signature nominale. Le moteur de règles et les modèles IA surveillent en continu."
          />
        )}
      </div>
      {!compact && (
        <div className="mt-2.5 flex items-center gap-2 border-t border-white/[0.06] pt-2.5 text-[10px] text-[rgb(var(--muted))]">
          <CheckCircle2 size={12} className="text-[#00E396]" />
          Règles seuils + détection d'anomalie + classification de défaut, anti-spam 60 s
        </div>
      )}
    </Panel>
  );
}
