/**
 * Compliance dashboard — DO-178C objectives summary on the left,
 * REQ node browser on the right.
 */
import { useQuery } from '@tanstack/react-query';
import {
  ShieldCheck, ShieldAlert, AlertTriangle, CheckCircle2, Circle, RefreshCw,
} from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { ResizableSplit } from '@/components/ResizableSplit';

// ── Types ──────────────────────────────────────────────────────────────────────

interface ComplianceData {
  enabled: boolean;
  standard?: string;
  dal?: string;
  total_requirements?: number;
  total_tests?: number;
  untraced_requirements?: number;
  compliance_percent?: number;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, ok }: {
  label: string; value: string | number; sub?: string; ok?: boolean;
}) {
  const color = ok === undefined ? 'text-forge-text' : ok ? 'text-forge-success' : 'text-forge-error';
  return (
    <div className="p-4 rounded-xl border border-forge-border bg-forge-bg/50">
      <h3 className="text-[10px] font-mono text-forge-muted uppercase tracking-widest mb-2">{label}</h3>
      <div className="flex items-end gap-1.5">
        <span className={`text-2xl font-bold font-mono ${color}`}>{value}</span>
        {sub && <span className="text-forge-muted text-xs mb-0.5">{sub}</span>}
      </div>
    </div>
  );
}

function ObjectiveRow({ label, done }: { label: string; done: boolean }) {
  return (
    <div className="flex items-center gap-3 py-2 border-b border-forge-border/50 last:border-0">
      {done
        ? <CheckCircle2 size={13} className="text-forge-success shrink-0" />
        : <Circle       size={13} className="text-forge-muted/40 shrink-0" />}
      <span className={`text-xs font-mono ${done ? 'text-forge-text' : 'text-forge-muted'}`}>{label}</span>
      <span className={`ml-auto text-[10px] font-mono uppercase shrink-0 ${done ? 'text-forge-success' : 'text-forge-muted'}`}>
        {done ? 'COMPLETE' : 'PENDING'}
      </span>
    </div>
  );
}

// ── Main dashboard ─────────────────────────────────────────────────────────────

const REQ_TYPES = new Set(['REQ']);

export function Compliance() {
  const { data, isLoading, error, refetch } = useQuery<ComplianceData>({
    queryKey: ['compliance'],
    queryFn: () => fetch('/api/graph/compliance').then(r => r.json()),
    refetchInterval: 10_000,
  });

  const { data: allNodes = [], isLoading: nodesLoading, refetch: refetchNodes } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const reqNodes = allNodes.filter(n => REQ_TYPES.has(n.node_type));

  const pct       = data?.compliance_percent   ?? 0;
  const untraced  = data?.untraced_requirements ?? 0;
  const total     = data?.total_requirements    ?? 0;
  const totalTests= data?.total_tests           ?? 0;
  const traced    = total - untraced;
  const isReady   = pct >= 100 && total > 0;

  const objectives = [
    { label: 'All requirements documented in graph',  done: total > 0 },
    { label: 'Requirements traced to test cases',      done: untraced === 0 && total > 0 },
    { label: 'Test cases cover all LLR',               done: totalTests >= total && total > 0 },
    { label: 'No untraced requirements',               done: untraced === 0 && total > 0 },
    { label: 'Compliance threshold ≥ 100%',            done: pct >= 100 && total > 0 },
  ];

  function refetchAll() { refetch(); refetchNodes(); }

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Compliance</h1>
          <p className="text-sm text-forge-muted font-mono">
            DO-178C objective tracking and certification readiness.
            {data?.standard && (
              <span className="ml-2 text-forge-accent">
                {data.standard} DAL-{data.dal}
              </span>
            )}
          </p>
        </div>
        <button
          onClick={refetchAll}
          className="p-2 rounded-lg bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-text transition-colors"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Loading / error states */}
      {isLoading && (
        <div className="shrink-0 bg-forge-surface rounded-xl border border-forge-border p-8 flex items-center justify-center">
          <RefreshCw size={20} className="text-forge-muted animate-spin" />
        </div>
      )}
      {error && (
        <div className="shrink-0 bg-forge-error/5 border border-forge-error/20 rounded-xl p-4 text-center">
          <p className="text-forge-error font-mono text-sm">Failed to load compliance data.</p>
        </div>
      )}
      {data && !data.enabled && (
        <div className="shrink-0 bg-forge-surface rounded-xl border border-forge-border p-10 flex flex-col items-center text-center">
          <AlertTriangle size={36} className="text-forge-warning mb-3 opacity-60" />
          <h2 className="text-base font-bold text-forge-text mb-1">Compliance Disabled</h2>
          <p className="text-forge-muted font-mono text-sm">
            Enable compliance checking in forge.toml under [compliance].
          </p>
        </div>
      )}

      {/* Two-panel: NTP left (graph-first), compliance summary right */}
      {data?.enabled && (
        <div className="flex-1 min-h-0">
          <ResizableSplit
            initialSplit={62}
            minLeft={20}
            maxLeft={80}
            storageKey="compliance"
            left={
              <div className="flex flex-col min-h-0 h-full pr-2">
                <NodeTablePanel
                  nodes={reqNodes}
                  isLoading={nodesLoading}
                  title="Requirements"
                  icon={<ShieldCheck size={14} />}
                  onRefresh={refetchNodes}
                  emptyMessage="No REQ nodes yet. Run phases 1–2 to generate requirements."
                  extraDetail={(node) => <NodeContextPanel node={node} />}
                />
              </div>
            }
            right={
              <div className="flex flex-col h-full pl-2 overflow-y-auto gap-4">
            {/* Status banner */}
            <div className={`rounded-xl border p-5 flex items-center gap-4 shrink-0 ${
              isReady
                ? 'bg-forge-success/5 border-forge-success/30'
                : 'bg-forge-warning/5 border-forge-warning/30'
            }`}>
              {isReady
                ? <ShieldCheck size={32} className="text-forge-success shrink-0" />
                : <ShieldAlert size={32} className="text-forge-warning shrink-0" />}
              <div className="flex-1 min-w-0">
                <h2 className="text-base font-bold font-mono text-forge-text">
                  {isReady ? 'Certification Ready' : 'Certification Incomplete'}
                </h2>
                <p className="text-xs font-mono text-forge-muted mt-0.5">
                  {isReady
                    ? 'All objectives satisfied.'
                    : `${untraced} untraced requirement${untraced !== 1 ? 's' : ''}.`}
                </p>
              </div>
              <div className="text-right shrink-0">
                <span className={`text-4xl font-bold font-mono ${isReady ? 'text-forge-success' : 'text-forge-warning'}`}>
                  {pct.toFixed(1)}%
                </span>
                <p className="text-[10px] font-mono text-forge-muted mt-0.5">compliance</p>
              </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 gap-2 shrink-0">
              <StatCard label="Total Reqs"   value={total}       sub="nodes"        />
              <StatCard label="Traced"        value={traced}      sub="reqs"    ok={traced === total && total > 0} />
              <StatCard label="Untraced"      value={untraced}    sub="reqs"    ok={untraced === 0} />
              <StatCard label="Test Cases"    value={totalTests}  sub="cases"        />
            </div>

            {/* DO-178C objectives */}
            <div className="bg-forge-surface rounded-xl border border-forge-border overflow-hidden shrink-0">
              <div className="p-3 border-b border-forge-border bg-forge-bg/50 flex items-center gap-2">
                <ShieldCheck size={14} className="text-forge-muted" />
                <h2 className="text-sm font-bold font-mono text-forge-text uppercase">DO-178C Objectives</h2>
                <span className="ml-auto text-[10px] font-mono text-forge-muted">
                  {objectives.filter(o => o.done).length}/{objectives.length} satisfied
                </span>
              </div>
              <div className="p-3">
                {objectives.map((obj, i) => (
                  <ObjectiveRow key={i} label={obj.label} done={obj.done} />
                ))}
              </div>
            </div>
              </div>
            }
          />
        </div>
      )}
    </div>
  );
}
