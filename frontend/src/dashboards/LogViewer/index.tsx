/**
 * Log Viewer — structured log browser for the FORGE observability store.
 *
 * Filters on top, sortable table below, detail drawer on the right.
 * Polls GET /api/v1/logs every 5s. Decisions are highlighted; errors
 * bubble to the top visually.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, AlertTriangle, Info, Search, X } from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface LogRecord {
  id: number;
  ts_ms: number;
  level: string;
  category: string;
  msg: string;
  detail: string | null;
  run_id: string | null;
  phase: number | null;
  cycle: number | null;
  gap_type: string | null;
  gap_id: string | null;
  node_id: string | null;
  agent_id: string | null;
  call_id: string | null;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  tool_call_count: number | null;
  tool_name: string | null;
  duration_ms: number | null;
  error_type: string | null;
  extras: Record<string, unknown> | null;
}

interface LogQueryResponse {
  total: number;
  records: LogRecord[];
  dropped_since: { count: number; ts_ms: number | null };
}

// ── Constants ─────────────────────────────────────────────────────────────────

const LEVELS = ['DEBUG', 'INFO', 'WARN', 'ERROR'] as const;

const CATEGORIES = [
  'SYS', 'LOOP', 'PHASE', 'PIPE', 'FLOW', 'BATCH',
  'GAP', 'GAPF', 'AGENT', 'POOL', 'CREW', 'LLM', 'TOOL', 'THROT',
  'DECIDE',
  'GRAPH', 'STORE', 'SYNC',
  'QUAL', 'SEMA', 'RQUAL', 'CQUAL', 'CONS', 'CONSIST', 'DECOMP', 'COV', 'CTRC', 'CTX',
  'CGEN', 'SCAN', 'BZEL', 'EVAL', 'AUDIT', 'QUEUE', 'DLVR',
  'USER', 'AUTH', 'HTTP', 'WS',
];

const TIME_PRESETS = [
  { label: '5 min', value: '-5m' },
  { label: '30 min', value: '-30m' },
  { label: '1 hr', value: '-1h' },
  { label: '3 hrs', value: '-3h' },
  { label: '1 day', value: '-1d' },
  { label: '3 days', value: '-3d' },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTs(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString('en-GB', { hour12: false }) + '.' +
    String(d.getMilliseconds()).padStart(3, '0');
}

function levelColour(level: string): string {
  switch (level) {
    case 'ERROR': return 'text-red-400 bg-red-500/10';
    case 'WARN':  return 'text-yellow-300 bg-yellow-500/10';
    case 'INFO':  return 'text-sky-300 bg-sky-500/10';
    case 'DEBUG': return 'text-forge-muted bg-forge-border/30';
    default:      return 'text-forge-muted';
  }
}

function levelIcon(level: string) {
  switch (level) {
    case 'ERROR': return <AlertCircle size={11} />;
    case 'WARN':  return <AlertTriangle size={11} />;
    case 'INFO':  return <Info size={11} />;
    default:      return null;
  }
}

// ── Filter state ──────────────────────────────────────────────────────────────

interface Filters {
  levels: string[];
  categories: string[];
  runId: string;
  phase: string;
  gapType: string;
  nodeId: string;
  callId: string;
  since: string;
  q: string;
}

const EMPTY_FILTERS: Filters = {
  levels: [],
  categories: [],
  runId: '',
  phase: '',
  gapType: '',
  nodeId: '',
  callId: '',
  since: '-1h',
  q: '',
};

function buildQueryParams(f: Filters, limit: number): URLSearchParams {
  const p = new URLSearchParams();
  f.levels.forEach(l => p.append('level', l));
  f.categories.forEach(c => p.append('category', c));
  if (f.runId) p.append('run_id', f.runId);
  if (f.phase) p.append('phase', f.phase);
  if (f.gapType) p.append('gap_type', f.gapType);
  if (f.nodeId) p.append('node_id', f.nodeId);
  if (f.callId) p.append('call_id', f.callId);
  if (f.since) p.append('since', f.since);
  if (f.q) p.append('q', f.q);
  p.append('limit', String(limit));
  return p;
}

// ── Main component ────────────────────────────────────────────────────────────

export function LogViewer() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<LogRecord | null>(null);
  const [paused, setPaused] = useState(false);
  const [limit] = useState(500);

  const params = useMemo(() => buildQueryParams(filters, limit), [filters, limit]);

  const { data, isLoading, refetch, isFetching } = useQuery<LogQueryResponse>({
    queryKey: ['logs', params.toString()],
    queryFn: async () => {
      const r = await fetch(`/api/v1/logs?${params.toString()}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    refetchInterval: paused ? false : 5000,
  });

  const records = data?.records ?? [];
  const total = data?.total ?? 0;
  const dropped = data?.dropped_since?.count ?? 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <FilterBar
        filters={filters}
        setFilters={setFilters}
        total={total}
        dropped={dropped}
        paused={paused}
        setPaused={setPaused}
        refreshing={isFetching}
        refetch={() => refetch()}
      />
      <div className="flex-1 flex overflow-hidden">
        <LogTable
          records={records}
          loading={isLoading}
          selected={selected}
          onSelect={setSelected}
          onFilterAdd={(key, val) => setFilters(f => ({ ...f, [key]: val }))}
        />
        {selected && (
          <LogDetail
            record={selected}
            onClose={() => setSelected(null)}
            onFilterAdd={(key, val) => setFilters(f => ({ ...f, [key]: val }))}
          />
        )}
      </div>
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

interface FilterBarProps {
  filters: Filters;
  setFilters: (updater: (f: Filters) => Filters) => void;
  total: number;
  dropped: number;
  paused: boolean;
  setPaused: (p: boolean) => void;
  refreshing: boolean;
  refetch: () => void;
}

function FilterBar({ filters, setFilters, total, dropped, paused, setPaused, refreshing, refetch }: FilterBarProps) {
  const toggle = (key: 'levels' | 'categories', value: string) => {
    setFilters(f => ({
      ...f,
      [key]: f[key].includes(value) ? f[key].filter(x => x !== value) : [...f[key], value],
    }));
  };
  const setField = (key: keyof Filters, value: string) => {
    setFilters(f => ({ ...f, [key]: value }));
  };

  return (
    <div className="border-b border-forge-border p-3 space-y-2 bg-forge-bg">
      {/* Row 1: levels, time presets, search, stats */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-mono text-forge-muted/70">level</span>
        {LEVELS.map(l => {
          const active = filters.levels.includes(l);
          return (
            <button
              key={l}
              onClick={() => toggle('levels', l)}
              className={`px-2 py-0.5 rounded text-[11px] font-mono border transition-colors ${
                active
                  ? `${levelColour(l)} border-current`
                  : 'border-forge-border text-forge-muted hover:text-forge-text'
              }`}
            >
              {l}
            </button>
          );
        })}

        <span className="mx-1 text-forge-border">·</span>
        <span className="text-xs font-mono text-forge-muted/70">since</span>
        {TIME_PRESETS.map(p => (
          <button
            key={p.value}
            onClick={() => setField('since', p.value)}
            className={`px-2 py-0.5 rounded text-[11px] font-mono border transition-colors ${
              filters.since === p.value
                ? 'bg-forge-accent/10 text-forge-accent border-forge-accent/30'
                : 'border-forge-border text-forge-muted hover:text-forge-text'
            }`}
          >
            {p.label}
          </button>
        ))}

        <span className="flex-1" />

        <div className="flex items-center gap-2 text-[11px] font-mono text-forge-muted">
          <span className="text-forge-text">{total.toLocaleString()}</span> rows
          {dropped > 0 && (
            <span className="text-yellow-300" title="Records dropped under backpressure">
              · {dropped} dropped
            </span>
          )}
          <button
            onClick={() => setPaused(!paused)}
            className="ml-2 px-2 py-0.5 rounded border border-forge-border text-forge-muted hover:text-forge-text"
          >
            {paused ? '▶ live' : '⏸ pause'}
          </button>
          <button
            onClick={refetch}
            disabled={refreshing}
            className="px-2 py-0.5 rounded border border-forge-border text-forge-muted hover:text-forge-text disabled:opacity-40"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Row 2: text search + structured filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-xs">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-forge-muted" />
          <input
            type="text"
            placeholder="search msg / detail…"
            value={filters.q}
            onChange={e => setField('q', e.target.value)}
            className="w-full pl-7 pr-2 py-1 bg-forge-border/30 border border-forge-border rounded text-xs text-forge-text placeholder-forge-muted/50 focus:outline-none focus:border-forge-accent/50"
          />
        </div>
        <FilterInput label="run_id" value={filters.runId} onChange={v => setField('runId', v)} />
        <FilterInput label="phase" value={filters.phase} onChange={v => setField('phase', v)} width="w-16" />
        <FilterInput label="gap_type" value={filters.gapType} onChange={v => setField('gapType', v)} />
        <FilterInput label="node_id" value={filters.nodeId} onChange={v => setField('nodeId', v)} />
        <FilterInput label="call_id" value={filters.callId} onChange={v => setField('callId', v)} />
        {(filters.runId || filters.phase || filters.gapType || filters.nodeId || filters.callId || filters.q) && (
          <button
            onClick={() => setFilters(() => EMPTY_FILTERS)}
            className="text-[11px] text-forge-muted hover:text-forge-text underline"
          >
            clear
          </button>
        )}
      </div>

      {/* Row 3: categories (collapsible) */}
      <CategoryChips
        selected={filters.categories}
        onToggle={c => toggle('categories', c)}
      />
    </div>
  );
}

function FilterInput({ label, value, onChange, width = 'w-32' }: {
  label: string; value: string; onChange: (v: string) => void; width?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[11px] font-mono text-forge-muted/70">{label}</span>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        className={`${width} px-1.5 py-0.5 bg-forge-border/30 border border-forge-border rounded text-[11px] font-mono text-forge-text focus:outline-none focus:border-forge-accent/50`}
      />
    </div>
  );
}

function CategoryChips({ selected, onToggle }: { selected: string[]; onToggle: (c: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? CATEGORIES : CATEGORIES.slice(0, 12);
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <span className="text-[11px] font-mono text-forge-muted/70 mr-1">category</span>
      {visible.map(c => {
        const active = selected.includes(c);
        return (
          <button
            key={c}
            onClick={() => onToggle(c)}
            className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-colors ${
              active
                ? 'bg-forge-accent/10 text-forge-accent border-forge-accent/30'
                : 'border-forge-border text-forge-muted hover:text-forge-text'
            }`}
          >
            {c}
          </button>
        );
      })}
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-[10px] text-forge-muted hover:text-forge-text ml-1"
      >
        {expanded ? '− less' : `+ ${CATEGORIES.length - 12} more`}
      </button>
    </div>
  );
}

// ── Table ─────────────────────────────────────────────────────────────────────

interface LogTableProps {
  records: LogRecord[];
  loading: boolean;
  selected: LogRecord | null;
  onSelect: (r: LogRecord) => void;
  onFilterAdd: (key: keyof Filters, value: string) => void;
}

function LogTable({ records, loading, selected, onSelect, onFilterAdd }: LogTableProps) {
  return (
    <div className="flex-1 overflow-auto font-mono text-[11px]">
      <table className="w-full table-fixed">
        <thead className="sticky top-0 bg-forge-bg border-b border-forge-border text-forge-muted/60">
          <tr>
            <th className="w-[92px] px-2 py-1 text-left font-normal">time</th>
            <th className="w-[56px] px-2 py-1 text-left font-normal">lvl</th>
            <th className="w-[80px] px-2 py-1 text-left font-normal">cat</th>
            <th className="w-[44px] px-2 py-1 text-left font-normal">ph</th>
            <th className="w-[140px] px-2 py-1 text-left font-normal">gap_type</th>
            <th className="w-[120px] px-2 py-1 text-left font-normal">node_id</th>
            <th className="w-[110px] px-2 py-1 text-left font-normal">model</th>
            <th className="w-[88px] px-2 py-1 text-right font-normal">tokens</th>
            <th className="w-[68px] px-2 py-1 text-right font-normal">dur</th>
            <th className="px-2 py-1 text-left font-normal">msg</th>
          </tr>
        </thead>
        <tbody>
          {loading && records.length === 0 && (
            <tr><td colSpan={10} className="px-3 py-6 text-center text-forge-muted">loading…</td></tr>
          )}
          {!loading && records.length === 0 && (
            <tr><td colSpan={10} className="px-3 py-6 text-center text-forge-muted">no records match</td></tr>
          )}
          {records.map(r => {
            const isSelected = selected?.id === r.id;
            return (
              <tr
                key={r.id}
                onClick={() => onSelect(r)}
                className={`border-b border-forge-border/30 cursor-pointer ${
                  isSelected
                    ? 'bg-forge-accent/10'
                    : 'hover:bg-forge-border/20'
                }`}
              >
                <td className="px-2 py-0.5 text-forge-muted whitespace-nowrap">{formatTs(r.ts_ms)}</td>
                <td className="px-2 py-0.5">
                  <span className={`px-1 rounded inline-flex items-center gap-0.5 ${levelColour(r.level)}`}>
                    {levelIcon(r.level)} {r.level}
                  </span>
                </td>
                <td className="px-2 py-0.5 text-forge-muted">{r.category}</td>
                <td className="px-2 py-0.5 text-forge-muted text-center">{r.phase ?? ''}</td>
                <td className="px-2 py-0.5 truncate">
                  {r.gap_type && (
                    <button
                      className="text-sky-300/80 hover:text-sky-200"
                      onClick={e => { e.stopPropagation(); onFilterAdd('gapType', r.gap_type!); }}
                    >
                      {r.gap_type}
                    </button>
                  )}
                </td>
                <td className="px-2 py-0.5 truncate">
                  {r.node_id && (
                    <button
                      className="text-emerald-300/80 hover:text-emerald-200"
                      onClick={e => { e.stopPropagation(); onFilterAdd('nodeId', r.node_id!); }}
                    >
                      {r.node_id}
                    </button>
                  )}
                </td>
                <td className="px-2 py-0.5 truncate text-cyan-400/80" title={r.model ?? undefined}>
                  {r.model ?? ''}
                </td>
                <td className="px-2 py-0.5 text-right text-forge-muted tabular-nums">
                  {r.prompt_tokens != null || r.completion_tokens != null
                    ? `${r.prompt_tokens ?? 0}→${r.completion_tokens ?? 0}`
                    : ''}
                </td>
                <td className="px-2 py-0.5 text-right text-forge-muted">
                  {r.duration_ms != null ? `${r.duration_ms}ms` : ''}
                </td>
                <td className="px-2 py-0.5 truncate text-forge-text">{r.msg}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

interface LogDetailProps {
  record: LogRecord;
  onClose: () => void;
  onFilterAdd: (key: keyof Filters, value: string) => void;
}

function LogDetail({ record, onClose, onFilterAdd }: LogDetailProps) {
  const structured: [string, unknown][] = [
    ['ts', new Date(record.ts_ms).toISOString()],
    ['level', record.level],
    ['category', record.category],
    ['run_id', record.run_id],
    ['phase', record.phase],
    ['cycle', record.cycle],
    ['gap_type', record.gap_type],
    ['gap_id', record.gap_id],
    ['node_id', record.node_id],
    ['agent_id', record.agent_id],
    ['call_id', record.call_id],
    ['model', record.model],
    ['tool_name', record.tool_name],
    ['duration_ms', record.duration_ms],
    ['prompt_tokens', record.prompt_tokens],
    ['completion_tokens', record.completion_tokens],
    ['tool_call_count', record.tool_call_count],
    ['error_type', record.error_type],
  ];
  const filterable: Record<string, keyof Filters> = {
    run_id: 'runId', phase: 'phase', gap_type: 'gapType',
    node_id: 'nodeId', call_id: 'callId',
  };

  return (
    <aside className="w-[440px] border-l border-forge-border overflow-y-auto font-mono text-[11px]">
      <header className="sticky top-0 bg-forge-bg border-b border-forge-border p-2 flex items-center justify-between">
        <span className="text-forge-muted uppercase tracking-wider text-[10px]">record #{record.id}</span>
        <button onClick={onClose} className="text-forge-muted hover:text-forge-text">
          <X size={14} />
        </button>
      </header>

      <section className="p-3 space-y-2 border-b border-forge-border">
        <div className="text-forge-text text-sm leading-tight">{record.msg}</div>
        {record.detail && (
          <pre className="text-forge-muted whitespace-pre-wrap text-[11px] leading-relaxed">
            {record.detail}
          </pre>
        )}
      </section>

      <section className="p-3 space-y-1 border-b border-forge-border">
        {structured.filter(([, v]) => v != null && v !== '').map(([k, v]) => {
          const filterKey = filterable[k];
          return (
            <div key={k} className="flex gap-2">
              <span className="w-[110px] text-forge-muted/70 shrink-0">{k}</span>
              <span className="flex-1 text-forge-text break-all">{String(v)}</span>
              {filterKey && (
                <button
                  onClick={() => onFilterAdd(filterKey, String(v))}
                  className="text-forge-accent/70 hover:text-forge-accent text-[10px]"
                  title="filter by this value"
                >
                  filter
                </button>
              )}
            </div>
          );
        })}
      </section>

      {record.extras && Object.keys(record.extras).length > 0 && (
        <section className="p-3">
          <div className="text-forge-muted/70 mb-1">extras</div>
          <pre className="text-forge-text whitespace-pre-wrap text-[11px] leading-relaxed">
            {JSON.stringify(record.extras, null, 2)}
          </pre>
        </section>
      )}
    </aside>
  );
}
