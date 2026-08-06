/**
 * PhaseBreadcrumb — the pipeline rail shown at the top of every page.
 *
 * All 15 phases as a connected track. Status is encoded in form, not just
 * colour: complete = check, active = spinner, awaiting_approval = pause bars,
 * pending = hollow. Structural gap counts and live wall time ride along.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { Check, Loader, Pause } from 'lucide-react';
import { useStore } from '@/store';
import { PHASE_CONFIG, NUM_PHASES } from '@/lib/phaseConfig';
import { PHASE_COLOR } from '@/lib/nodeColors';
import { gapCountsByPhase } from '@/lib/pipeline';
import { phaseElapsedMs, formatDuration } from '@/lib/phaseTiming';

/** Ticks once a second while the build runs so live durations advance. */
function useClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [enabled]);
  return now;
}

export function PhaseBreadcrumb() {
  const navigate = useNavigate();
  const location = useLocation();
  const { phases, phaseTimes, gaps, session } = useStore();
  const reducedMotion = useReducedMotion();

  const phaseMatch = location.pathname.match(/\/phase\/(\d+)/);
  const currentPhase = phaseMatch ? parseInt(phaseMatch[1], 10) : null;
  const isBuilding = session.loopStatus === 'RUNNING';
  const now = useClock(isBuilding);
  const gapCounts = gapCountsByPhase(gaps);

  const activePhase = Object.entries(phases).find(([, p]) => p.status === 'active');
  const activeNum = activePhase ? parseInt(activePhase[0], 10) : null;

  return (
    <div className="shrink-0 flex items-center h-11 px-3 border-b border-forge-border/60 bg-forge-surface/60">
      {/* Build status */}
      <div className="flex items-center gap-1.5 mr-3 pr-3 border-r border-forge-border/60 shrink-0">
        {isBuilding ? (
          <Loader size={11} className="text-forge-warning animate-spin" />
        ) : (
          <div className="w-1.5 h-1.5 rounded-full bg-forge-faint/60" />
        )}
        <span className="text-[9px] font-mono text-forge-muted uppercase tracking-widest">
          {isBuilding ? 'Building' : 'Idle'}
        </span>
        {session.iterationCount > 0 && (
          <span className="text-[9px] font-mono text-forge-faint tabular-nums">
            #{session.iterationCount}
          </span>
        )}
      </div>

      {/* Phase track */}
      <div className="flex items-center gap-[3px] flex-1 max-w-[640px] min-w-0">
        {Array.from({ length: NUM_PHASES }, (_, i) => {
          const status = phases[i]?.status ?? 'pending';
          const config = PHASE_CONFIG[i];
          const isViewing = currentPhase === i;
          const phaseColor = PHASE_COLOR[i];
          const isComplete = status === 'complete';
          const isActive = status === 'active';
          const isAwaiting = status === 'awaiting_approval';
          const gapCount = gapCounts[i] ?? 0;
          const elapsed = phaseElapsedMs(phaseTimes[i], now);

          return (
            <button
              key={i}
              onClick={() => navigate(`/phase/${i}`)}
              className="group relative flex-1 h-8 flex items-center justify-center cursor-pointer min-w-0"
              aria-label={`Phase ${i} ${config.name} — ${status}`}
            >
              {/* Track segment */}
              <motion.div
                className={`w-full h-3 rounded-[3px] ${isViewing ? 'ring-1 ring-forge-text/30 ring-offset-1 ring-offset-forge-bg' : ''}`}
                initial={false}
                animate={{
                  backgroundColor:
                    isComplete || isActive || isAwaiting
                      ? phaseColor
                      : 'rgba(128,140,155,0.14)',
                  opacity: isComplete ? 1 : isActive ? 0.95 : isAwaiting ? 0.7 : 1,
                }}
                transition={reducedMotion ? { duration: 0 } : { duration: 0.35 }}
              />

              {/* Status glyph — form, not just colour */}
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                {isComplete && <Check size={9} className="text-white/80" strokeWidth={3.5} />}
                {isActive && <Loader size={9} className="text-white/90 animate-spin" strokeWidth={3} />}
                {isAwaiting && <Pause size={8} className="text-white/90" strokeWidth={3.5} />}
              </div>

              {/* Gap count pip */}
              {gapCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-[13px] h-[13px] px-0.5 rounded-full bg-forge-warning text-forge-bg text-[8px] font-mono font-bold flex items-center justify-center leading-none pointer-events-none">
                  {gapCount > 99 ? '99+' : gapCount}
                </span>
              )}

              {/* Tooltip */}
              <div className="absolute top-full mt-1 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                <div className="whitespace-nowrap bg-forge-raised border border-forge-border rounded-md px-2 py-1 shadow-xl">
                  <div className="flex items-center gap-1.5">
                    <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: phaseColor }} />
                    <span className="text-[9px] font-mono text-forge-text">{i} {config.name}</span>
                    <span className={`text-[8px] font-mono ${
                      isComplete ? 'text-forge-success' :
                      isActive ? 'text-forge-warning' :
                      isAwaiting ? 'text-forge-accent' :
                      'text-forge-faint'
                    }`}>
                      {status.replace('_', ' ')}
                    </span>
                    {gapCount > 0 && (
                      <span className="text-[8px] font-mono text-forge-warning">{gapCount} gap{gapCount !== 1 ? 's' : ''}</span>
                    )}
                    {elapsed !== null && (
                      <span className="text-[8px] font-mono text-forge-muted tabular-nums">{formatDuration(elapsed)}</span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Active phase readout */}
      {(activeNum !== null || currentPhase !== null) && (
        <div className="ml-3 pl-3 border-l border-forge-border/60 flex items-center gap-1.5 min-w-0 shrink-0">
          <div
            className="w-1 h-3.5 rounded-full shrink-0"
            style={{ backgroundColor: PHASE_COLOR[activeNum ?? currentPhase ?? 0] }}
          />
          <span className="text-[9px] font-mono text-forge-muted truncate">
            {PHASE_CONFIG[activeNum ?? currentPhase ?? 0]?.name}
          </span>
          {activeNum !== null && phaseElapsedMs(phaseTimes[activeNum], now) !== null && (
            <span className="text-[9px] font-mono text-forge-warning tabular-nums shrink-0">
              {formatDuration(phaseElapsedMs(phaseTimes[activeNum], now) ?? 0)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
