import { useEffect, useState, useCallback, useRef } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from '@/components/Sidebar';
import { ConsoleBar } from '@/components/ConsoleBar';
import { WorkQueuePanel } from '@/components/WorkQueuePanel';
import { useForgeSocket } from '@/hooks/useForgeSocket';
import { useStore } from '@/store';

const STORAGE_KEY_HEIGHT = 'layout.bottomHeight';
const STORAGE_KEY_SPLIT = 'layout.consoleSplit';

export function Layout() {
  useForgeSocket();
  const location = useLocation();
  const logUserAction = useStore((s) => s.logUserAction);
  const consoleOpen = useStore((s) => s.consoleOpen);

  useEffect(() => {
    logUserAction(`Navigate → ${location.pathname}`);
  }, [location.pathname, logUserAction]);

  // Bottom panel height (shared by console + work queue)
  const [bottomHeight, setBottomHeight] = useState(() => {
    return parseInt(localStorage.getItem(STORAGE_KEY_HEIGHT) || '300', 10);
  });
  const bottomHeightRef = useRef(bottomHeight);
  bottomHeightRef.current = bottomHeight;
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_HEIGHT, String(bottomHeight));
  }, [bottomHeight]);

  // Console/queue horizontal split (percentage for console)
  const [consoleSplit, setConsoleSplit] = useState(() => {
    return parseInt(localStorage.getItem(STORAGE_KEY_SPLIT) || '70', 10);
  });
  const consoleSplitRef = useRef(consoleSplit);
  consoleSplitRef.current = consoleSplit;
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_SPLIT, String(consoleSplit));
  }, [consoleSplit]);

  // Vertical resize (bottom panel height)
  const containerRef = useRef<HTMLDivElement>(null);
  const onVerticalResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = bottomHeightRef.current;
    const onMove = (ev: MouseEvent) => {
      const delta = startY - ev.clientY;
      const maxH = Math.round(window.innerHeight * 0.8);
      setBottomHeight(Math.max(100, Math.min(maxH, startH + delta)));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, []);

  // Horizontal resize (console/queue split)
  const onHorizontalResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const onMove = (ev: MouseEvent) => {
      const pct = Math.round(((ev.clientX - rect.left) / rect.width) * 100);
      setConsoleSplit(Math.max(30, Math.min(85, pct)));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, []);

  return (
    <div className="flex h-screen bg-forge-bg text-forge-text overflow-hidden font-sans">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 overflow-auto bg-forge-bg relative">
          <Outlet />
        </main>

        {/* Bottom panel: console + work queue side by side */}
        <div
          className="shrink-0 border-t border-forge-border"
          style={consoleOpen ? { height: bottomHeight } : undefined}
        >
          {/* Vertical resize handle */}
          {consoleOpen && (
            <div
              className="h-1.5 cursor-row-resize hover:bg-forge-accent/20 active:bg-forge-accent/40 transition-colors shrink-0"
              onMouseDown={onVerticalResize}
              title="Drag to resize height"
            />
          )}

          <div
            ref={containerRef}
            className="flex h-full"
            style={consoleOpen ? { height: bottomHeight - 6 } : undefined}
          >
            {/* Console */}
            <div style={{ width: `${consoleSplit}%` }} className="min-w-0">
              <ConsoleBar />
            </div>

            {/* Horizontal resize handle */}
            {consoleOpen && (
              <div
                className="w-1 cursor-col-resize hover:bg-forge-accent/20 active:bg-forge-accent/40 transition-colors shrink-0"
                onMouseDown={onHorizontalResize}
                title="Drag to resize split"
              />
            )}

            {/* Work Queue */}
            <div style={{ width: `${100 - consoleSplit}%` }} className="min-w-0">
              <WorkQueuePanel />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
