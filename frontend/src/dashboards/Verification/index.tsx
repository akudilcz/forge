import { useQuery } from '@tanstack/react-query';
import { useStore } from '@/store';
import { TestTube2, CheckCircle2, XCircle, Clock, RefreshCw, Play } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { ResizableSplit } from '@/components/ResizableSplit';

const TEST_TYPES = new Set(['SUITE', 'CASE_HLR', 'CASE_LLR', 'TEST', 'RESULT']);

interface TestSummary {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  coverage_percent: number | null;
  last_run: string | null;
  status: 'not_started' | 'running' | 'passed' | 'failed';
}

function StatCard({ label, value, sub, color }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="p-4 rounded-xl border border-forge-border bg-forge-surface">
      <h3 className="text-[10px] font-mono text-forge-muted uppercase tracking-widest mb-3">{label}</h3>
      <div className="flex items-end gap-2">
        <span className={`text-3xl font-bold font-mono ${color ?? 'text-forge-text'}`}>{value}</span>
        {sub && <span className="text-forge-muted text-xs mb-0.5">{sub}</span>}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: TestSummary['status'] }) {
  const map = {
    not_started: { label: 'Not Run',  cls: 'text-forge-muted border-forge-border',         icon: <Clock size={11} /> },
    running:     { label: 'Running',  cls: 'text-forge-warning border-forge-warning/30',   icon: <RefreshCw size={11} className="animate-spin" /> },
    passed:      { label: 'Passed',   cls: 'text-forge-success border-forge-success/30',   icon: <CheckCircle2 size={11} /> },
    failed:      { label: 'Failed',   cls: 'text-forge-error border-forge-error/30',       icon: <XCircle size={11} /> },
  } as const;
  const cfg = map[status] ?? map.not_started;
  return (
    <span className={`flex items-center gap-1.5 text-xs font-mono px-3 py-1 rounded-full border ${cfg.cls}`}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

export function Verification() {
  const { gaps } = useStore();
  const testGaps = gaps.filter(g => g.type === 'UNTESTED_CODE' || g.type === 'UNEXECUTED_CASE');

  const { data: summary, isLoading: summaryLoading, refetch: refetchSummary } = useQuery<TestSummary>({
    queryKey: ['test-summary'],
    queryFn: () => fetch('/api/workspace/tests/summary').then(r => r.json()),
    refetchInterval: 15_000,
  });

  const { data: allNodes, isLoading: nodesLoading, refetch: refetchNodes } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const testNodes = (allNodes ?? []).filter(n => TEST_TYPES.has(n.node_type));

  const coverage = summary?.coverage_percent != null
    ? `${summary.coverage_percent.toFixed(1)}%` : '—';
  const verPct = summary && summary.total > 0
    ? `${((summary.passed / summary.total) * 100).toFixed(1)}%` : '—';
  const coverageColor = summary?.coverage_percent != null
    ? summary.coverage_percent >= 90 ? 'text-forge-success'
      : summary.coverage_percent >= 70 ? 'text-forge-warning' : 'text-forge-error'
    : 'text-forge-muted';

  function refetchAll() { refetchSummary(); refetchNodes(); }

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Verification</h1>
          <p className="text-sm text-forge-muted font-mono">
            Test suites, cases and execution results.
            {summary?.last_run && (
              <span className="ml-2 text-forge-muted/60">
                Last run: {new Date(summary.last_run).toLocaleTimeString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {summary && <StatusBadge status={summary.status} />}
          <button
            onClick={refetchAll}
            className="p-2 rounded-lg bg-forge-surface border border-forge-border text-forge-muted hover:text-forge-text transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Two-panel body — NTP left (graph-first), stats right */}
      <div className="flex-1 min-h-0">
        <ResizableSplit
          initialSplit={62}
          minLeft={20}
          maxLeft={80}
          storageKey="verification"
          left={
            <div className="flex flex-col min-h-0 h-full pr-2">
              <NodeTablePanel
                nodes={testNodes}
                isLoading={nodesLoading}
                title="Test Nodes"
                icon={<TestTube2 size={14} />}
                onRefresh={refetchNodes}
                emptyMessage="No test nodes yet. Run phases 9-12 to generate SUITE, CASE_HLR, CASE_LLR and TEST nodes."
                extraDetail={(node) => <NodeContextPanel node={node} />}
              />
            </div>
          }
          right={
            <div className="flex flex-col h-full pl-2 overflow-y-auto gap-4">
          {/* Stats */}
          {!summaryLoading && summary && (
            <div className="grid grid-cols-2 gap-3 shrink-0">
              <StatCard label="Coverage" value={coverage} sub="of code" color={coverageColor} />
              <StatCard
                label="Tests Passed"
                value={verPct}
                sub="of suite"
                color={summary.total > 0 && summary.failed === 0 ? 'text-forge-success' : 'text-forge-error'}
              />
              <StatCard label="Total Tests" value={summary.total} sub="cases" />
              <StatCard
                label="Open Gaps"
                value={testGaps.length}
                sub="items"
                color={testGaps.length > 0 ? 'text-forge-warning' : 'text-forge-success'}
              />
            </div>
          )}

          {/* Progress bar */}
          {summary && summary.total > 0 && (
            <div className="shrink-0 bg-forge-surface rounded-xl border border-forge-border p-4">
              <div className="flex items-center gap-3 mb-3">
                <TestTube2 size={14} className="text-forge-muted" />
                <span className="text-xs font-bold font-mono text-forge-text uppercase">Suite Progress</span>
              </div>
              <div className="h-2.5 rounded-full bg-forge-bg overflow-hidden flex">
                <div className="bg-forge-success transition-all" style={{ width: `${(summary.passed / summary.total) * 100}%` }} />
                <div className="bg-forge-error transition-all"   style={{ width: `${(summary.failed / summary.total) * 100}%` }} />
                <div className="bg-forge-muted/30 transition-all" style={{ width: `${(summary.skipped / summary.total) * 100}%` }} />
              </div>
              <div className="flex gap-5 mt-2 text-[10px] font-mono">
                <span className="flex items-center gap-1 text-forge-success"><CheckCircle2 size={10} /> {summary.passed} passed</span>
                <span className="flex items-center gap-1 text-forge-error"><XCircle size={10} /> {summary.failed} failed</span>
                <span className="flex items-center gap-1 text-forge-muted"><Clock size={10} /> {summary.skipped} skipped</span>
              </div>
            </div>
          )}

          {/* No tests yet */}
          {summary?.status === 'not_started' && testNodes.length === 0 && (
            <div className="bg-forge-surface rounded-xl border border-forge-border p-8 flex flex-col items-center text-center shrink-0">
              <Play size={28} className="text-forge-muted mb-3 opacity-40" />
              <p className="text-forge-text font-mono font-bold mb-1 text-sm">No tests run yet</p>
              <p className="text-forge-muted font-mono text-xs">
                The test engineer agent generates SUITE, CASE_HLR and CASE_LLR nodes automatically during verification.
              </p>
            </div>
          )}
            </div>
          }
        />
      </div>
    </div>
  );
}
