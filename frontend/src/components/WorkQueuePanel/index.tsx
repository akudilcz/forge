import { useRef, useEffect, useState } from 'react';
import { useStore, type WorkQueueItem, type WorkQueueAction } from '@/store';
import { ListChecks, ChevronDown, ChevronRight } from 'lucide-react';

const STATUS_STYLE: Record<string, string> = {
  pending: 'text-slate-400 bg-slate-400/10 border-slate-400/20',
  in_progress: 'text-blue-400 bg-blue-400/10 border-blue-400/20',
  done: 'text-green-400 bg-green-400/10 border-green-400/20',
  failed: 'text-red-400 bg-red-400/10 border-red-400/20',
};

const URGENCY_STYLE: Record<string, string> = {
  critical: 'text-red-400 bg-red-400/10 border-red-400/30',
  high: 'text-orange-400 bg-orange-400/10 border-orange-400/30',
  medium: 'text-slate-400 bg-slate-400/10 border-slate-400/30',
  low: 'text-slate-500 bg-slate-500/10 border-slate-500/30',
};

const IMPORTANCE_LABEL: Record<string, string> = {
  high: 'IMP',
  medium: '',
  low: '',
};

const STATUS_ICON: Record<string, string> = {
  pending: '\u00B7',
  in_progress: '\u25B6',
  done: '\u2713',
  failed: '\u2717',
};

const EFFORT_STYLE: Record<string, string> = {
  low: 'text-green-400 bg-green-400/10 border-green-400/20',
  medium: 'text-amber-400 bg-amber-400/10 border-amber-400/20',
  high: 'text-red-400 bg-red-400/10 border-red-400/20',
};

const OUTCOME_STYLE: Record<string, string> = {
  improved: 'text-green-400',
  no_change: 'text-amber-400',
  worse: 'text-red-400',
};

function QueueItem({ item }: { item: WorkQueueItem }) {
  const [expanded, setExpanded] = useState(false);
  const isActive = item.status === 'in_progress';

  return (
    <div
      className={`px-2 py-1.5 border-b border-forge-border/20 ${
        isActive ? 'bg-blue-400/5' : ''
      }`}
    >
      <div className="flex items-center gap-1.5 cursor-pointer" onClick={() => setExpanded(!expanded)}>
        {/* Urgency badge */}
        <span
          className={`text-[8px] font-mono font-bold px-1 py-0.5 rounded border shrink-0 ${
            URGENCY_STYLE[item.urgency] ?? URGENCY_STYLE.medium
          }`}
        >
          {item.urgency === 'critical' ? 'CRIT' : item.urgency === 'high' ? 'HIGH' : item.urgency === 'medium' ? 'MED' : 'LOW'}
        </span>

        {/* Importance badge (only show if high) */}
        {item.importance === 'high' && (
          <span className="text-[7px] font-mono font-bold text-amber-400">
            {IMPORTANCE_LABEL[item.importance]}
          </span>
        )}

        {/* Status icon */}
        <span
          className={`text-[10px] font-bold w-3 text-center ${
            STATUS_STYLE[item.status]?.split(' ')[0] ?? 'text-slate-400'
          } ${isActive ? 'animate-pulse' : ''}`}
        >
          {STATUS_ICON[item.status] ?? '\u00B7'}
        </span>

        {/* Category chip */}
        <span className="text-[9px] font-mono font-bold px-1 py-0.5 rounded border border-cyan-400/30 bg-cyan-400/10 text-cyan-400 shrink-0">
          {item.category}
        </span>

        {/* Description */}
        <span className="text-[11px] text-forge-text truncate flex-1">
          {item.target || item.description}
        </span>

        {/* Effort chip */}
        <span
          className={`text-[8px] font-mono font-bold px-1 py-0.5 rounded border shrink-0 ${
            EFFORT_STYLE[item.effort] ?? EFFORT_STYLE.medium
          }`}
        >
          {item.effort}
        </span>

        {/* Expand toggle */}
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-forge-muted shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 text-forge-muted shrink-0" />
        )}
      </div>

      {expanded && (
        <div className="ml-8 mt-1 space-y-0.5">
          {item.target && item.target !== item.description && (
            <p className="text-[10px] text-forge-muted">{item.description}</p>
          )}
          {item.rationale && (
            <p className="text-[10px] text-forge-muted italic">{item.rationale}</p>
          )}
          {item.affected_files.length > 0 && (
            <p className="text-[9px] text-forge-muted font-mono">
              {item.affected_files.join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function HistoryEntry({ action }: { action: WorkQueueAction }) {
  return (
    <div className="px-2 py-1 flex items-center gap-1.5 text-[10px]">
      <span className="text-forge-muted font-mono w-6 text-right shrink-0">
        R{action.round}
      </span>
      <span className={OUTCOME_STYLE[action.outcome] ?? 'text-forge-muted'}>
        {action.outcome === 'improved' ? '\u2713' : action.outcome === 'worse' ? '\u2717' : '\u2013'}
      </span>
      <span className="font-mono text-cyan-400">{action.category}</span>
      <span className="text-forge-muted truncate flex-1">
        {action.summary}
      </span>
      <span className="text-forge-muted font-mono shrink-0">
        {action.gap_count_before}\u2192{action.gap_count_after}
      </span>
    </div>
  );
}

export function WorkQueuePanel() {
  const items = useStore((s) => s.workQueue);
  const history = useStore((s) => s.workQueueHistory);
  const [historyOpen, setHistoryOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to active item
  useEffect(() => {
    if (scrollRef.current) {
      const activeEl = scrollRef.current.querySelector('[data-active="true"]');
      activeEl?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [items]);

  const pending = items.filter((i) => i.status === 'pending').length;
  const done = items.filter((i) => i.status === 'done').length;
  const failed = items.filter((i) => i.status === 'failed').length;
  const total = items.length;

  return (
    <div className="flex flex-col h-full border-l border-forge-border/30 bg-forge-bg">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-forge-border/30 bg-forge-bg/40 shrink-0">
        <ListChecks className="w-3.5 h-3.5 text-forge-muted" />
        <span className="text-[11px] font-semibold text-forge-text">Work Queue</span>
        {total > 0 && (
          <span className="text-[9px] font-mono text-forge-muted ml-auto">
            {done}/{total}
            {failed > 0 && <span className="text-red-400 ml-1">{failed}F</span>}
            {pending > 0 && <span className="text-slate-400 ml-1">{pending}P</span>}
          </span>
        )}
      </div>

      {/* Queue items */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto scrollbar-thin"
      >
        {total === 0 ? (
          <div className="flex items-center justify-center h-full text-[11px] text-forge-muted/50">
            No items queued
          </div>
        ) : (
          items.map((item) => (
            <div key={item.id} data-active={item.status === 'in_progress'}>
              <QueueItem item={item} />
            </div>
          ))
        )}
      </div>

      {/* History section */}
      {history.length > 0 && (
        <div className="border-t border-forge-border/30">
          <button
            onClick={() => setHistoryOpen(!historyOpen)}
            className="w-full flex items-center gap-1.5 px-3 py-1 text-[10px] text-forge-muted hover:text-forge-text"
          >
            {historyOpen ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            History ({history.length})
          </button>
          {historyOpen && (
            <div className="max-h-32 overflow-y-auto scrollbar-thin border-t border-forge-border/20">
              {history.map((action, i) => (
                <HistoryEntry key={i} action={action} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
