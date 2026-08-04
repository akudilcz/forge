/**
 * PhaseDashboard — generic dashboard for a single FORGE phase.
 *
 * URL: /phase/:phaseNum  (0–13)
 * Left:  NodeTablePanel filtered to the phase's node types.
 * Right: Phase info, live status, gap list, and Run Phase button.
 */

import { useMemo, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, AlertCircle, PlayCircle, AlertTriangle, Gauge, RefreshCw } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { ResizableSplit } from '@/components/ResizableSplit';
import { useStore, type PhaseStatus } from '@/store';
import { PHASE_CONFIG, GAP_AGENT_ROLE, GAP_PHASE, QUALITY_GAP_TYPES } from '@/lib/phaseConfig';
import { PHASE_COLOR, TYPE_COLOR } from '@/lib/nodeColors';
import CodeGenPanel from './CodeGenPanel';
import DeliverablesPanel from './DeliverablesPanel';
import DocoRenderPanel from './DocoRenderPanel';
import { ForgemdUpload } from './ForgemdUpload';

// ── Status badge ──────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<PhaseStatus, string> = {
  pending:           'text-slate-400 bg-slate-400/10 border-slate-400/20',
  active:            'text-amber-400 bg-amber-400/10 border-amber-400/20',
  complete:          'text-green-400 bg-green-400/10 border-green-400/20',
  awaiting_approval: 'text-blue-400 bg-blue-400/10 border-blue-400/20',
  skipped:           'text-slate-500 bg-slate-500/10 border-slate-500/20',
};

function StatusBadge({ status }: { status: PhaseStatus | undefined }) {
  const s = status ?? 'pending';
  const style = STATUS_STYLE[s] ?? STATUS_STYLE.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-mono font-bold uppercase ${style}`}>
      {s === 'active' && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
      {s === 'complete' && <CheckCircle2 size={11} />}
      {s}
    </span>
  );
}

// ── Gap row ───────────────────────────────────────────────────────────────────

function GapRow({ type, node_id, description, qual = false }: { type: string; node_id: string; description: string; qual?: boolean }) {
  const badgeCls = qual
    ? 'border-violet-500/30 text-violet-400 bg-violet-500/10'
    : 'border-amber-400/30 text-amber-400 bg-amber-400/10';
  return (
    <div className="flex items-start gap-2 py-2 border-b border-forge-border/30 last:border-0">
      <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border shrink-0 mt-0.5 ${badgeCls}`}>
        {type.replace(/_/g, ' ')}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-mono text-forge-muted/70 truncate">{node_id}</p>
        <p className="text-xs font-mono text-forge-text/80 leading-snug mt-0.5">{description}</p>
        {GAP_AGENT_ROLE[type] && (
          <p className="text-[9px] font-mono text-forge-muted/50 mt-0.5">→ {GAP_AGENT_ROLE[type]}</p>
        )}
      </div>
    </div>
  );
}

// ── Gap section (structural or quality) ───────────────────────────────────────

function GapSection({ label, gaps, qual, emptyStatus, className = '' }: {
  label: string;
  gaps: Array<{ type: string; node_id: string; description: string }>;
  qual: boolean;
  emptyStatus?: PhaseStatus | undefined;
  className?: string;
}) {
  const accentCls = qual ? 'text-violet-400 bg-violet-400/20' : 'text-amber-400 bg-amber-400/20';
  const emptyAccentCls = qual ? 'bg-forge-border text-violet-400/50' : 'bg-forge-border text-forge-muted';
  return (
    <div className={className}>
      <h3 className="text-[10px] font-mono text-forge-muted uppercase tracking-wider mb-3 flex items-center gap-2">
        {label}
        <span className={`px-1.5 py-0.5 rounded-full text-[9px] font-mono font-bold ${gaps.length > 0 ? accentCls : emptyAccentCls}`}>
          {gaps.length}
        </span>
      </h3>
      {gaps.length === 0 ? (
        emptyStatus === 'complete' || emptyStatus === 'skipped' ? (
          <div className="flex items-center gap-2 text-sm font-mono text-forge-success/70">
            <CheckCircle2 size={14} />
            Phase complete — no outstanding gaps
          </div>
        ) : (
          <p className="text-sm font-mono text-forge-muted/50 italic">
            {emptyStatus === 'pending' || emptyStatus == null ? 'Phase not yet started.' : 'No active gaps detected.'}
          </p>
        )
      ) : (
        <div>
          {gaps.map(g => (
            <GapRow key={`${g.type}:${g.node_id}`} type={g.type} node_id={g.node_id} description={g.description} qual={qual} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Phase Info Panel ──────────────────────────────────────────────────────────

const QUALITY_GAP_SET = new Set<string>(QUALITY_GAP_TYPES);

function PhaseInfoPanel({ phaseNum, config, status, gaps, allNodes, onRunPhase, isRunning, onRefresh }: {
  phaseNum: number;
  config: typeof PHASE_CONFIG[0];
  status: PhaseStatus | undefined;
  gaps: Array<{ type: string; node_id: string; description: string }>;
  allNodes: GNode[];
  onRunPhase?: () => void;
  isRunning: boolean;
  onRefresh?: () => void;
}) {
  const Icon = config.icon;
  const nodeTypeById = Object.fromEntries(allNodes.map(n => [n.node_id, n.node_type]));
  const phaseNodeTypeSet = new Set(config.nodeTypes);

  const phaseGaps = gaps.filter(g => {
    if (GAP_PHASE[g.type] === phaseNum) return true;
    if (config.qualCheck && QUALITY_GAP_SET.has(g.type)) {
      const nodeType = nodeTypeById[g.node_id];
      return nodeType != null && phaseNodeTypeSet.has(nodeType);
    }
    return false;
  });

  const structuralGaps = phaseGaps.filter(g => !QUALITY_GAP_SET.has(g.type));
  const qualityGaps    = phaseGaps.filter(g =>  QUALITY_GAP_SET.has(g.type));

  return (
    <div className="flex flex-col bg-forge-surface rounded-xl border border-forge-border overflow-hidden h-full">
      {/* Header */}
      <div className="p-5 border-b border-forge-border bg-forge-bg/50 shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-lg border flex items-center justify-center shrink-0"
              style={{ backgroundColor: `${PHASE_COLOR[phaseNum]}1a`, borderColor: `${PHASE_COLOR[phaseNum]}33` }}
            >
              <Icon size={18} style={{ color: PHASE_COLOR[phaseNum] }} />
            </div>
            <div>
              <p className="text-[10px] font-mono text-forge-muted uppercase tracking-wider">
                Phase {phaseNum}
              </p>
              <h2 className="text-lg font-bold font-mono text-forge-text">{config.name}</h2>
            </div>
          </div>
          <StatusBadge status={status} />
        </div>
        <p className="text-sm font-mono text-forge-muted mt-3 leading-relaxed">
          {config.description}
        </p>
      </div>

      {/* Action buttons */}
      {onRunPhase && (
        <div className="px-5 py-3 border-b border-forge-border/50 shrink-0 flex items-center gap-2">
          <button
            onClick={onRunPhase}
            disabled={isRunning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-xs font-mono font-bold"
            title="Run phase"
          >
            <PlayCircle size={12} /> Run Phase
          </button>
        </div>
      )}

      {/* Phase 1: forge.md upload zone */}
      {phaseNum === 1 && <ForgemdUpload onUploaded={onRefresh} />}

      {/* Node types badge row */}
      {config.nodeTypes.length > 0 && (
        <div className="px-5 py-3 border-b border-forge-border/50 flex items-center gap-2 shrink-0">
          <span className="text-[9px] font-mono text-forge-muted uppercase tracking-wider">Produces</span>
          {config.nodeTypes.map(t => (
            <span key={t} className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${TYPE_COLOR[t] ?? 'text-slate-400 bg-slate-400/15 border-slate-400/30'}`}>
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Gap list */}
      <div className="flex-1 overflow-y-auto p-5">
        <GapSection
          label="Structural Gaps"
          gaps={structuralGaps}
          qual={false}
          emptyStatus={phaseGaps.length === 0 ? status : undefined}
        />
        {config.qualCheck && (
          <GapSection
            label="Quality Issues"
            gaps={qualityGaps}
            qual={true}
            className="mt-5"
          />
        )}

        {/* Gap types reference */}
        {(config.gapTypes.length > 0 || config.qualCheck) && (
          <div className="mt-6">
            <p className="text-[9px] font-mono text-forge-muted/50 uppercase tracking-wider mb-2">Gap types in this phase</p>
            <div className="flex flex-wrap gap-1">
              {config.gapTypes.map(g => (
                <span key={g} className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-forge-border/50 text-forge-muted/60">
                  {g}
                </span>
              ))}
              {config.qualCheck && QUALITY_GAP_TYPES.map(g => (
                <span key={g} className="text-[9px] font-mono px-1.5 py-0.5 rounded border border-violet-500/30 text-violet-400/60">
                  {g}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Phase 12 stats bar ───────────────────────────────────────────────────

function CodeGenStatsBar({ nodes, status, onRunPhase, onSyncTraces, isRunning }: {
  nodes: GNode[];
  status: PhaseStatus | undefined;
  onRunPhase: () => void;
  onSyncTraces: () => void;
  isRunning: boolean;
}) {
  const CODE_TYPES = new Set(['DESIGN', 'CASE_HLR', 'CASE_LLR']);
  const tracedNodes = nodes.filter(n =>
    CODE_TYPES.has(n.node_type) && n.properties?.file_path && n.properties?.line_traces);
  const stats = tracedNodes.reduce((acc, n) => {
    const fp = n.properties.file_path as string;
    const c = n.properties.trace_coverage as { total: number; traced: number } | undefined;
    const u = n.properties.untraced_functions as unknown[] | undefined;
    const llrs = (n.properties.traced_llrs as string[]) ?? [];
    for (const id of llrs) acc.coveredLlrs.add(id);
    const isSrc = fp.startsWith('src/');
    return {
      ...acc,
      src: acc.src + (isSrc ? 1 : 0),
      test: acc.test + (fp.startsWith('tests/') ? 1 : 0),
      total: acc.total + (isSrc ? (c?.total ?? 0) : 0),
      traced: acc.traced + (isSrc ? (c?.traced ?? 0) : 0),
      gaps: acc.gaps + (u?.length ?? 0),
    };
  }, { src: 0, test: 0, total: 0, traced: 0, gaps: 0, coveredLlrs: new Set<string>() });
  const funcPct = stats.total > 0 ? Math.round((stats.traced / stats.total) * 100) : null;

  // Line coverage from bazel LCOV report (via workspace scanner)
  const lineCov = useStore(s => s.lineCoverage);
  const linePct = lineCov !== null ? Math.round(lineCov) : null;

  // MC/DC (branch) coverage from bazel LCOV report
  const branchCov = useStore(s => s.branchCoverage);
  const branchPct = branchCov !== null ? Math.round(branchCov) : null;

  // Requirements coverage: how many LLR nodes are covered by traced_llrs
  const llrNodeIds = new Set(nodes.filter(n => n.node_type === 'LLR').map(n => n.node_id));
  const coveredLlrs = [...stats.coveredLlrs].filter(id => llrNodeIds.has(id)).length;
  const totalLlrs = llrNodeIds.size;
  const reqPct = totalLlrs > 0 ? Math.round((coveredLlrs / totalLlrs) * 100) : null;

  return (
    <div className="px-4 py-2 border-b border-forge-border/50 shrink-0 flex items-center gap-3 flex-wrap">
      <StatusBadge status={status} />
      <div className="flex items-center gap-3 text-[10px] font-mono text-forge-muted">
          <span>{stats.src} src</span>
          <span>{stats.test} test</span>
          <span className="text-forge-border">|</span>
          <span className="text-cyan-500/70 text-[9px] uppercase">lines</span>
          <span className="text-cyan-400 flex items-center gap-0.5">
            <Gauge size={9} />{linePct !== null ? `${linePct}%` : '–'}
          </span>
          <span className="text-violet-500/70 text-[9px] uppercase">funcs</span>
          <span className="text-violet-400 flex items-center gap-0.5">
            {funcPct !== null ? <><Gauge size={9} />{funcPct}%</> : `${stats.traced}/${stats.total}`}
          </span>
          <span className="text-emerald-500/70 text-[9px] uppercase">reqs</span>
          <span className="text-emerald-400 flex items-center gap-0.5">
            {reqPct !== null ? <><Gauge size={9} />{reqPct}%</> : `${coveredLlrs}/${totalLlrs}`}
          </span>
          <span className="text-amber-500/70 text-[9px] uppercase">MC/DC</span>
          <span className="text-amber-400 flex items-center gap-0.5">
            {branchPct !== null ? <><Gauge size={9} />{branchPct}%</> : '–'}
          </span>
          {stats.gaps > 0 && (
            <span className="text-red-400 flex items-center gap-0.5">
              <AlertTriangle size={9} />{stats.gaps} gap{stats.gaps !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      <div className="flex items-center gap-2 ml-auto">
        <button
          onClick={onSyncTraces}
          disabled={isRunning}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/15 text-blue-400 border border-blue-500/30 hover:bg-blue-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-xs font-mono font-bold"
          title="Re-parse files on disk and sync trace data. Clears traces for missing files."
        >
          <RefreshCw size={12} /> Sync Traces
        </button>
        <button
          onClick={onRunPhase}
          disabled={isRunning}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-xs font-mono font-bold"
        >
          <PlayCircle size={12} /> Run Phase
        </button>
      </div>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function PhaseDashboard() {
  const { phaseNum: phaseParam } = useParams<{ phaseNum: string }>();
  const phaseNum = parseInt(phaseParam ?? '0', 10);
  const config = PHASE_CONFIG[phaseNum];

  const { phases, gaps, session } = useStore();
  const isRunning = session.loopStatus === 'RUNNING';
  const phaseStatus = phases[phaseNum]?.status;

  const logAction = useStore((s) => s.logUserAction);
  const handleRunPhase = async () => {
    logAction(`Run phase ${phaseNum}`);
    await fetch(`/api/phases/${phaseNum}/run`, { method: 'POST' });
  };

  const handleSyncTraces = async () => {
    logAction('Sync traces (phase 12)');
    await fetch('/api/phases/12/sync-traces', { method: 'POST' });
    refetch();
  };

  const { data: allNodes = [], isLoading, refetch } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 10_000,
  });

  if (!config) {
    return (
      <div className="h-full flex items-center justify-center text-forge-muted font-mono">
        <AlertCircle size={16} className="mr-2" />
        Unknown phase: {phaseParam}
      </div>
    );
  }

  // Phases with empty nodeTypes (e.g. Dashboard) show all nodes; otherwise filter
  const nodeTypeSet = useMemo(() => new Set(config.nodeTypes), [config.nodeTypes]);
  const phaseNodes = useMemo(
    () => nodeTypeSet.size === 0 ? allNodes : allNodes.filter(n => nodeTypeSet.has(n.node_type)),
    [allNodes, nodeTypeSet],
  );

  // Phase 12 left panel shows requirements + cases + designs
  const REQ_TYPES = useMemo(() => new Set(['LLR', 'HLR', 'CASE_HLR', 'CASE_LLR', 'DESIGN']), []);
  const reqNodes = useMemo(
    () => allNodes.filter(n => REQ_TYPES.has(n.node_type)),
    [allNodes, REQ_TYPES],
  );

  // Phase 12 multi-select for requirement filtering
  const [selectedReqIds, setSelectedReqIds] = useState<Set<string>>(new Set());

  const handleNodeToggle = useCallback((node: GNode, shiftKey: boolean) => {
    setSelectedReqIds(prev => {
      if (shiftKey) {
        const next = new Set(prev);
        if (next.has(node.node_id)) next.delete(node.node_id);
        else next.add(node.node_id);
        return next;
      }
      // No shift: if sole selection → deselect; otherwise set as sole
      if (prev.size === 1 && prev.has(node.node_id)) return new Set();
      return new Set([node.node_id]);
    });
  }, []);

  const handleResetSelection = useCallback(() => {
    setSelectedReqIds(new Set());
  }, []);

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3">
        <div className="w-1 h-8 rounded-full shrink-0" style={{ backgroundColor: PHASE_COLOR[phaseNum] }} />
        <h1 className="text-2xl font-bold font-mono text-forge-text">
          Phase {phaseNum} · {config.name}
        </h1>
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0">
        {phaseNum === 12 ? (
          /* Phase 12: requirement tree left, code traceability right */
          <ResizableSplit
            initialSplit={35}
            minLeft={20}
            maxLeft={50}
            storageKey="phase-12"
            left={
              <div className="flex flex-col min-h-0 h-full pr-2">
                <NodeTablePanel
                  nodes={reqNodes}
                  isLoading={isLoading}
                  title="Requirements & Cases"
                  icon={<config.icon size={14} />}
                  onRefresh={refetch}
                  emptyMessage="No requirements yet — run earlier phases first."
                  selectedIds={selectedReqIds}
                  onNodeToggle={handleNodeToggle}
                  onResetSelection={handleResetSelection}
                />
              </div>
            }
            right={
              <div className="flex flex-col min-h-0 h-full pl-2">
                <div className="flex flex-col h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
                  <CodeGenStatsBar
                    nodes={allNodes}
                    status={phaseStatus}
                    onRunPhase={handleRunPhase}
                    onSyncTraces={handleSyncTraces}
                    isRunning={isRunning}
                  />
                  <div className="flex-1 min-h-0 overflow-hidden">
                    <CodeGenPanel nodes={allNodes} selectedRequirementIds={selectedReqIds} />
                  </div>
                </div>
              </div>
            }
          />
        ) : (
          /* All other phases: split NodeTablePanel + info/panel */
          <ResizableSplit
            initialSplit={40}
            minLeft={15}
            maxLeft={75}
            storageKey={`phase-${phaseNum}`}
            left={
              <div className="flex flex-col min-h-0 h-full pr-2">
                <NodeTablePanel
                  nodes={phaseNodes}
                  isLoading={isLoading}
                  title={config.name}
                  icon={<config.icon size={14} />}
                  onRefresh={refetch}
                  emptyMessage={`No ${config.nodeTypes.join('/') || 'nodes'} yet — run this phase to generate them.`}
                />
              </div>
            }
            right={
              <div className="flex flex-col min-h-0 h-full pl-2">
                {phaseNum === 11 || phaseNum === 14 ? (
                  <div className="flex flex-col h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
                    <div className="px-5 py-3 border-b border-forge-border/50 shrink-0 flex items-center gap-2">
                      <StatusBadge status={phaseStatus} />
                      <button
                        onClick={handleRunPhase}
                        disabled={isRunning}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25 transition-all disabled:opacity-40 disabled:cursor-not-allowed text-xs font-mono font-bold ml-auto"
                      >
                        <PlayCircle size={12} /> Run Phase
                      </button>
                    </div>
                    <div className="flex-1 min-h-0 overflow-hidden">
                      {phaseNum === 14 ? <DeliverablesPanel /> : <DocoRenderPanel />}
                    </div>
                  </div>
                ) : (
                  <PhaseInfoPanel
                    phaseNum={phaseNum}
                    config={config}
                    status={phaseStatus}
                    gaps={gaps}
                    allNodes={allNodes}
                    onRunPhase={handleRunPhase}
                    isRunning={isRunning}
                    onRefresh={refetch}
                  />
                )}
              </div>
            }
          />
        )}
      </div>
    </div>
  );
}
