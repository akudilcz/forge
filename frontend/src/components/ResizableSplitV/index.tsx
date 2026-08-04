/**
 * ResizableSplitV — vertical two-panel layout (top / bottom) with drag-to-resize and collapse.
 *
 * Hover the horizontal divider to reveal collapse buttons.
 * Split position is persisted to localStorage when storageKey is provided.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { ChevronUp, ChevronDown } from 'lucide-react';

interface Props {
  top: React.ReactNode;
  bottom: React.ReactNode;
  /** Top panel initial height as % of total (default 40). */
  initialSplit?: number;
  /** Minimum top panel % when dragging (default 10). */
  minTop?: number;
  /** Maximum top panel % when dragging (default 90). */
  maxTop?: number;
  /** localStorage key to persist split position. */
  storageKey?: string;
}

function readSplit(key: string | undefined, def: number): number {
  if (!key) return def;
  const v = parseFloat(localStorage.getItem(`rspv.${key}`) ?? '');
  return Number.isFinite(v) ? v : def;
}

export function ResizableSplitV({
  top, bottom,
  initialSplit = 40, minTop = 10, maxTop = 90, storageKey,
}: Props) {
  const [split, setSplit]         = useState(() => readSplit(storageKey, initialSplit));
  const [collapsed, setCollapsed] = useState<'top' | 'bottom' | null>(null);
  const [prior, setPrior]         = useState(initialSplit);
  const containerRef              = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (storageKey) localStorage.setItem(`rspv.${storageKey}`, String(Math.round(split)));
  }, [storageKey, split]);

  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const onMove = (ev: MouseEvent) => {
      const pct = ((ev.clientY - el.getBoundingClientRect().top) / el.offsetHeight) * 100;
      setSplit(Math.min(maxTop, Math.max(minTop, pct)));
      setCollapsed(null);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [minTop, maxTop]);

  const toggle = useCallback((side: 'top' | 'bottom') => {
    if (collapsed === side) { setSplit(prior); setCollapsed(null); }
    else { setPrior(split); setCollapsed(side); }
  }, [collapsed, prior, split]);

  const topClass = `min-w-0 overflow-hidden ${collapsed === 'bottom' ? 'flex-1' : 'shrink-0'}`;
  const topStyle = collapsed !== 'bottom' ? { height: collapsed === 'top' ? 0 : `${split}%` } : undefined;
  const bottomClass = `min-w-0 overflow-hidden ${collapsed === 'bottom' ? 'h-0 flex-none' : 'flex-1'}`;

  return (
    <div ref={containerRef} className="flex flex-col h-full min-h-0 w-full">
      <div className={topClass} style={topStyle}>{top}</div>

      {/* Drag handle */}
      <div
        className="h-3 shrink-0 flex items-center justify-center relative group cursor-row-resize select-none z-10"
        onMouseDown={startDrag}
      >
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-px bg-forge-border group-hover:bg-forge-accent/50 transition-colors" />
        <div className="relative z-10 flex flex-row gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            className="w-5 h-3.5 flex items-center justify-center rounded-sm bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-accent hover:border-forge-accent/40 transition-colors"
            title={collapsed === 'top' ? 'Expand top' : 'Collapse top'}
            onClick={e => { e.stopPropagation(); toggle('top'); }}
          >
            {collapsed === 'top' ? <ChevronDown size={8} /> : <ChevronUp size={8} />}
          </button>
          <button
            className="w-5 h-3.5 flex items-center justify-center rounded-sm bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-accent hover:border-forge-accent/40 transition-colors"
            title={collapsed === 'bottom' ? 'Expand bottom' : 'Collapse bottom'}
            onClick={e => { e.stopPropagation(); toggle('bottom'); }}
          >
            {collapsed === 'bottom' ? <ChevronUp size={8} /> : <ChevronDown size={8} />}
          </button>
        </div>
      </div>

      <div className={bottomClass}>{bottom}</div>
    </div>
  );
}
