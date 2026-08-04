/**
 * ResizableSplit — horizontal two-panel layout with drag-to-resize and collapse.
 *
 * Hover the divider to reveal collapse chevrons:
 *   ◀  collapse / expand the left panel
 *   ▶  collapse / expand the right panel
 *
 * Split position is persisted to localStorage when storageKey is provided.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface Props {
  left: React.ReactNode;
  right: React.ReactNode;
  /** Left panel initial width as % of total (default 35). */
  initialSplit?: number;
  /** Minimum left panel % when dragging (default 10). */
  minLeft?: number;
  /** Maximum left panel % when dragging (default 90). */
  maxLeft?: number;
  /** localStorage key to persist split position. */
  storageKey?: string;
}

function readSplit(key: string | undefined, def: number): number {
  if (!key) return def;
  const v = parseFloat(localStorage.getItem(`rsp.${key}`) ?? '');
  return Number.isFinite(v) ? v : def;
}

export function ResizableSplit({
  left, right,
  initialSplit = 35, minLeft = 10, maxLeft = 90, storageKey,
}: Props) {
  const [split, setSplit]       = useState(() => readSplit(storageKey, initialSplit));
  const [collapsed, setCollapsed] = useState<'left' | 'right' | null>(null);
  const [prior, setPrior]       = useState(initialSplit);
  const containerRef            = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (storageKey) localStorage.setItem(`rsp.${storageKey}`, String(Math.round(split)));
  }, [storageKey, split]);

  const startDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const el = containerRef.current;
    if (!el) return;
    const onMove = (ev: MouseEvent) => {
      const pct = ((ev.clientX - el.getBoundingClientRect().left) / el.offsetWidth) * 100;
      setSplit(Math.min(maxLeft, Math.max(minLeft, pct)));
      setCollapsed(null);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [minLeft, maxLeft]);

  const toggle = useCallback((side: 'left' | 'right') => {
    if (collapsed === side) { setSplit(prior); setCollapsed(null); }
    else { setPrior(split); setCollapsed(side); }
  }, [collapsed, prior, split]);

  const leftClass = `flex flex-col min-h-0 overflow-hidden ${collapsed === 'right' ? 'flex-1' : 'shrink-0'}`;
  const leftStyle = collapsed !== 'right' ? { width: collapsed === 'left' ? 0 : `${split}%` } : undefined;
  const rightClass = `flex flex-col min-h-0 overflow-hidden ${collapsed === 'right' ? 'w-0 flex-none' : 'flex-1'}`;

  return (
    <div ref={containerRef} className="flex h-full min-h-0 w-full min-w-0">
      <div className={leftClass} style={leftStyle}>{left}</div>

      {/* Drag handle */}
      <div
        className="w-3 shrink-0 flex items-center justify-center relative group cursor-col-resize select-none z-10"
        onMouseDown={startDrag}
      >
        <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-px bg-forge-border group-hover:bg-forge-accent/50 transition-colors" />
        <div className="relative z-10 flex flex-col gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            className="w-3.5 h-5 flex items-center justify-center rounded-sm bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-accent hover:border-forge-accent/40 transition-colors"
            title={collapsed === 'left' ? 'Expand left' : 'Collapse left'}
            onClick={e => { e.stopPropagation(); toggle('left'); }}
          >
            {collapsed === 'left' ? <ChevronRight size={8} /> : <ChevronLeft size={8} />}
          </button>
          <button
            className="w-3.5 h-5 flex items-center justify-center rounded-sm bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-accent hover:border-forge-accent/40 transition-colors"
            title={collapsed === 'right' ? 'Expand right' : 'Collapse right'}
            onClick={e => { e.stopPropagation(); toggle('right'); }}
          >
            {collapsed === 'right' ? <ChevronLeft size={8} /> : <ChevronRight size={8} />}
          </button>
        </div>
      </div>

      <div className={rightClass}>{right}</div>
    </div>
  );
}
