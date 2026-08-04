import { useState, useRef, useEffect, useCallback, type KeyboardEvent } from 'react';
import { Terminal, ChevronUp, ChevronDown, Send, Copy, Check, Trash2, MessageSquareX, Cpu } from 'lucide-react';
import { useStore, type LogEntry } from '@/store';

// ── Color maps ──────────────────────────────────────────────────────────────

const CAT_COLOR: Record<string, string> = {
  LOOP:  'text-forge-accent',
  PHASE: 'text-blue-400',
  SCAN:  'text-sky-400',
  GAP:   'text-amber-400',
  AGENT: 'text-purple-400',
  LLM:   'text-cyan-400',
  TOOL:  'text-green-400',
  CONS:  'text-teal-400',
  SEMA:  'text-pink-400',
  CREW:  'text-violet-400',
  QUAL:  'text-rose-400',
  AUDIT: 'text-indigo-400',
  FLOW:  'text-slate-400',
  CGEN:  'text-emerald-400',
  DASH:  'text-orange-400',
  USER:  'text-forge-success',
  SYS:   'text-forge-muted',
};

const LEVEL_COLOR: Record<string, string> = {
  INFO:  'text-forge-muted/60',
  WARN:  'text-forge-warning',
  ERROR: 'text-forge-error',
};

// ── Log row ─────────────────────────────────────────────────────────────────

function LogRow({ entry }: { entry: LogEntry }) {
  return (
    <div className="py-0.5 hover:bg-forge-bg/30 transition-colors">
      <div className="flex gap-1.5 items-start">
        <span className="shrink-0 text-forge-muted/30 tabular-nums text-[10px] w-[5.5rem]">{entry.ts}</span>
        <span className={`shrink-0 w-10 text-[9px] uppercase font-bold ${LEVEL_COLOR[entry.level] ?? 'text-forge-muted/60'}`}>
          {entry.level}
        </span>
        <span className={`shrink-0 w-12 text-[9px] uppercase font-bold ${CAT_COLOR[entry.cat] ?? 'text-forge-muted'}`}>
          {entry.cat}
        </span>
        <span className="flex-1 min-w-0 text-forge-text/80 text-[10px] break-words">{entry.msg}</span>
      </div>
      {entry.detail && (
        <div className="pl-[8.5rem] text-[10px] text-forge-muted/50 font-mono break-all">{entry.detail}</div>
      )}
    </div>
  );
}

// ── Log panel ───────────────────────────────────────────────────────────────

function ConsoleLog({ logs }: { logs: LogEntry[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stuckRef = useRef(true);

  const onScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    // "Stuck" = within 40px of the bottom
    stuckRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stuckRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [logs.length]);

  return (
    <div
      ref={containerRef}
      onScroll={onScroll}
      className="flex-1 overflow-y-auto px-3 py-2 font-mono bg-black/20 min-h-0 scrollbar-thin"
    >
      {logs.length === 0 && (
        <p className="text-forge-muted/40 text-[11px] italic pt-2">No activity yet.</p>
      )}
      {[...logs].reverse().map((entry, i) => <LogRow key={i} entry={entry} />)}
    </div>
  );
}

// ── Input row ───────────────────────────────────────────────────────────────

const HISTORY_KEY = 'console.history';
const MAX_HISTORY = 50;

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveHistory(history: string[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-MAX_HISTORY)));
}

function ConsoleInput({ onSubmit }: { onSubmit: (v: string) => void }) {
  const [value, setValue] = useState('');
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const draftRef = useRef('');

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    const updated = [...history, trimmed];
    setHistory(updated);
    saveHistory(updated);
    setHistoryIndex(-1);
    draftRef.current = '';
    onSubmit(trimmed);
    setValue('');
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') { submit(); return; }

    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length === 0) return;
      if (historyIndex === -1) draftRef.current = value;
      const next = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1);
      setHistoryIndex(next);
      setValue(history[next]);
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex === -1) return;
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(-1);
        setValue(draftRef.current);
      } else {
        setHistoryIndex(next);
        setValue(history[next]);
      }
      return;
    }
  }

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-t border-forge-border/30 shrink-0">
      <span className="text-teal-400/60 text-[11px] font-mono shrink-0">›</span>
      <input
        className="flex-1 bg-transparent text-[12px] text-forge-text placeholder:text-forge-muted/40 outline-none font-mono"
        placeholder="Type a request and press Enter — e.g. 'rename all HLRs to use The software shall'"
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={onKey}
      />
      <button
        onClick={submit}
        disabled={!value.trim()}
        className="shrink-0 text-forge-muted hover:text-teal-400 disabled:opacity-30 transition-colors"
        title="Run"
      >
        <Send size={14} />
      </button>
    </div>
  );
}

// ── LLM model badge (visible while LLM is working) ──────────────────────────

function LlmModelBadge() {
  const model = useStore(s => s.lastLlmModel);
  const busy = useStore(s => s.llmBusy);

  if (!model || !busy) return null;
  return (
    <span
      className="flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded
        border border-cyan-400/30 text-cyan-400 bg-cyan-400/10 animate-pulse"
    >
      <Cpu size={9} />
      {model}
    </span>
  );
}

// ── Context size badge ──────────────────────────────────────────────────────

function CtxBadge() {
  const tokens = useStore(s => s.lastCtxTokens);
  const ctxWindow = useStore(s => s.lastCtxWindow);

  if (!tokens || !ctxWindow) return null;
  const kb = (tokens / 250).toFixed(0);
  const pct = Math.round((tokens / ctxWindow) * 100);

  return (
    <span
      className="flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded
        border border-forge-muted/30 text-forge-muted bg-forge-muted/10"
      title={`${tokens.toLocaleString()} / ${ctxWindow.toLocaleString()} tokens (${pct}%)`}
    >
      {kb} KB
    </span>
  );
}

// ── Root component ───────────────────────────────────────────────────────────

export function ConsoleBar() {
  const { consoleOpen, toggleConsole, runConsole, logs, clearLogs, clearConversation } = useStore();
  const [copied, setCopied] = useState(false);

  function copyLogs() {
    const text = [...logs].reverse()
      .map(e => `[${e.ts}] [${e.level}] [${e.cat}] ${e.msg}${e.detail ? '\n  ' + e.detail : ''}`)
      .join('\n');
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div
      className="h-full bg-forge-surface/95 backdrop-blur-sm flex flex-col"
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-forge-border/30 shrink-0 px-1">
        <div className="flex items-center gap-1.5 px-2 py-1.5 shrink-0 text-teal-400">
          <Terminal size={13} />
          <span className="text-[10px] uppercase font-bold tracking-wider">Console</span>
          {logs.length > 0 && (
            <span className="text-[10px] font-mono text-forge-muted/40 ml-1">{logs.length}</span>
          )}
        </div>
        <CtxBadge />
        <LlmModelBadge />
        <div className="flex items-center gap-0.5 ml-auto pr-1">
          <button
            onClick={copyLogs}
            title="Copy log to clipboard"
            className="p-1.5 rounded text-forge-muted hover:text-forge-text transition-colors"
          >
            {copied ? <Check size={12} className="text-forge-success" /> : <Copy size={12} />}
          </button>
          <button
            onClick={clearConversation}
            title="Clear conversation history (agent forgets prior messages)"
            className="p-1.5 rounded text-forge-muted hover:text-forge-warning transition-colors"
          >
            <MessageSquareX size={12} />
          </button>
          {logs.length > 0 && (
            <button
              onClick={clearLogs}
              title="Clear log"
              className="p-1.5 rounded text-forge-muted hover:text-forge-error transition-colors"
            >
              <Trash2 size={12} />
            </button>
          )}
          <button
            onClick={toggleConsole}
            className="p-1.5 text-forge-muted hover:text-forge-text transition-colors"
            title={consoleOpen ? 'Collapse console' : 'Expand console'}
          >
            {consoleOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
        </div>
      </div>

      {consoleOpen && (
        <>
          <ConsoleLog logs={logs} />
          <ConsoleInput onSubmit={runConsole} />
        </>
      )}
    </div>
  );
}
