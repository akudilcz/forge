/**
 * PhaseBreadcrumb — sleek phase progress bar at the top of every page.
 *
 * 14 phases shown as a connected track with colored segments.
 * Completed phases fill with color, active phase pulses, pending phases are dim.
 * No layout shift on hover — tooltips float above without moving content.
 */

import { useNavigate, useLocation } from 'react-router-dom';
import { useStore } from '@/store';
import { PHASE_CONFIG } from '@/lib/phaseConfig';
import { PHASE_COLOR } from '@/lib/nodeColors';
import { Check, Loader } from 'lucide-react';

export function PhaseBreadcrumb() {
  const navigate = useNavigate();
  const location = useLocation();
  const { phases, session } = useStore();

  const phaseMatch = location.pathname.match(/\/phase\/(\d+)/);
  const currentPhase = phaseMatch ? parseInt(phaseMatch[1], 10) : null;
  const isBuilding = session.loopStatus === 'RUNNING';

  return (
    <div className="shrink-0 flex items-center h-10 px-3 border-b border-forge-border/20 bg-forge-bg">
      {/* Build status */}
      <div className="flex items-center gap-1.5 mr-3 pr-3 border-r border-forge-border/20">
        {isBuilding ? (
          <Loader size={10} className="text-amber-400 animate-spin" />
        ) : (
          <div className="w-1.5 h-1.5 rounded-full bg-forge-muted/30" />
        )}
        <span className="text-[8px] font-mono text-forge-muted/40 uppercase tracking-widest">
          {isBuilding ? 'Build' : 'Idle'}
        </span>
      </div>

      {/* Phase track */}
      <div className="flex items-center gap-px flex-1 max-w-[520px]">
        {Array.from({ length: 14 }, (_, i) => {
          const status = phases[i]?.status ?? 'pending';
          const config = PHASE_CONFIG[i];
          const isViewing = currentPhase === i;
          const phaseColor = PHASE_COLOR[i];
          const isComplete = status === 'complete';
          const isActive = status === 'active';

          return (
            <button
              key={i}
              onClick={() => navigate(`/phase/${i}`)}
              className="group relative flex-1 h-7 flex items-center justify-center cursor-pointer"
              title={`${i} ${config.name} — ${status}`}
            >
              {/* Track segment */}
              <div
                className={`
                  w-full h-2.5 rounded-sm transition-all duration-300
                  ${isActive ? 'animate-pulse' : ''}
                  ${isViewing ? 'ring-1 ring-white/20 ring-offset-1 ring-offset-forge-bg' : ''}
                `}
                style={{
                  backgroundColor: isComplete || isActive
                    ? phaseColor
                    : 'rgba(255,255,255,0.06)',
                  opacity: isComplete ? 1 : isActive ? 0.9 : 0.4,
                }}
              />

              {/* Status indicator overlay */}
              {isComplete && (
                <div className="absolute inset-0 flex items-center justify-center">
                  <Check size={8} className="text-white/70" strokeWidth={3} />
                </div>
              )}

              {/* Phase number — shows on hover */}
              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                <span
                  className="text-[7px] font-mono font-black"
                  style={{ color: phaseColor }}
                >
                  {i}
                </span>
              </div>

              {/* Tooltip */}
              <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                <div className="whitespace-nowrap bg-forge-surface border border-forge-border rounded-md px-2 py-1 shadow-xl">
                  <div className="flex items-center gap-1.5">
                    <div
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: phaseColor }}
                    />
                    <span className="text-[9px] font-mono text-forge-text">
                      {i} {config.name}
                    </span>
                    <span className={`text-[8px] font-mono ${
                      isComplete ? 'text-green-400' :
                      isActive ? 'text-amber-400' :
                      'text-forge-muted/50'
                    }`}>
                      {status}
                    </span>
                  </div>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Current phase label */}
      {currentPhase !== null && (
        <div className="ml-3 pl-3 border-l border-forge-border/20 flex items-center gap-1.5 min-w-0">
          <div
            className="w-1 h-3.5 rounded-full shrink-0"
            style={{ backgroundColor: PHASE_COLOR[currentPhase] }}
          />
          <span className="text-[9px] font-mono text-forge-muted/60 truncate">
            {PHASE_CONFIG[currentPhase]?.name}
          </span>
        </div>
      )}
    </div>
  );
}
