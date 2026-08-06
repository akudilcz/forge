/**
 * PipelineOverview — the 15-phase journey as the Command Centre hero.
 *
 * One card per phase: status in form (glyph) and colour, structural gap
 * count, and wall time. Cards navigate to the phase dashboard.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { Check, Loader, Pause, ChevronRight } from 'lucide-react';
import { useStore, type PhaseStatus } from '@/store';
import { PHASE_CONFIG, NUM_PHASES } from '@/lib/phaseConfig';
import { PHASE_COLOR } from '@/lib/nodeColors';
import { gapCountsByPhase } from '@/lib/pipeline';
import { phaseElapsedMs, formatDuration } from '@/lib/phaseTiming';

function StatusGlyph({ status }: { status: PhaseStatus }) {
  switch (status) {
    case 'complete':
      return <Check size={11} className="text-forge-success" strokeWidth={3} />;
    case 'active':
      return <Loader size={11} className="text-forge-warning animate-spin" strokeWidth={2.5} />;
    case 'awaiting_approval':
      return <Pause size={10} className="text-forge-accent" strokeWidth={3} />;
    case 'skipped':
      return <span className="block w-2 h-[2px] rounded bg-forge-faint" />;
    default:
      return <span className="block w-2 h-2 rounded-full border border-forge-faint/60" />;
  }
}

export function PipelineOverview() {
  const navigate = useNavigate();
  const reducedMotion = useReducedMotion();
  const { phases, phaseTimes, gaps, session } = useStore();
  const gapCounts = gapCountsByPhase(gaps);
  const isBuilding = session.loopStatus === 'RUNNING';

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isBuilding) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [isBuilding]);

  return (
    <div className="shrink-0">
      <div className="grid grid-cols-3 md:grid-cols-5 gap-2">
        {Array.from({ length: NUM_PHASES }, (_, i) => {
          const config = PHASE_CONFIG[i];
          const Icon = config.icon;
          const status = phases[i]?.status ?? 'pending';
          const color = PHASE_COLOR[i];
          const gapCount = gapCounts[i] ?? 0;
          const elapsed = phaseElapsedMs(phaseTimes[i], now);
          const isActive = status === 'active';
          const isDone = status === 'complete';

          return (
            <motion.button
              key={i}
              onClick={() => navigate(`/phase/${i}`)}
              initial={false}
              animate={isActive && !reducedMotion ? { scale: [1, 1.015, 1] } : { scale: 1 }}
              transition={isActive && !reducedMotion ? { duration: 2, repeat: Infinity } : { duration: 0 }}
              className={`group relative flex items-center gap-2.5 px-3 py-2.5 rounded-lg border text-left transition-colors border-forge-border ${
                isActive ? 'bg-forge-raised' : 'bg-forge-surface hover:bg-forge-raised'
              }`}
              style={isActive ? { borderColor: color } : isDone ? { borderColor: `${color}55` } : undefined}
            >
              {/* Phase colour spine */}
              <span
                className="w-1 self-stretch rounded-full shrink-0"
                style={{ backgroundColor: color, opacity: isDone || isActive ? 1 : 0.35 }}
              />
              <Icon size={15} className="shrink-0" style={{ color }} />
              <span className="flex-1 min-w-0">
                <span className="flex items-center gap-1.5">
                  <span className="text-[9px] font-mono text-forge-faint tabular-nums">{String(i).padStart(2, '0')}</span>
                  <span className="text-[11px] font-mono font-semibold text-forge-text truncate">{config.name}</span>
                </span>
                <span className="flex items-center gap-2 mt-0.5 h-3.5">
                  <StatusGlyph status={status} />
                  {gapCount > 0 && (
                    <span className="text-[9px] font-mono text-forge-warning">{gapCount} gap{gapCount !== 1 ? 's' : ''}</span>
                  )}
                  {elapsed !== null && (
                    <span className="text-[9px] font-mono text-forge-muted tabular-nums">{formatDuration(elapsed)}</span>
                  )}
                </span>
              </span>
              <ChevronRight size={12} className="text-forge-faint opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
            </motion.button>
          );
        })}
      </div>
    </div>
  );
}
