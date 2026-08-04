/**
 * TraceMatrixPanel — Column B of the Phase 12 dashboard.
 *
 * Contextual panel that adapts based on selection:
 * - File selected: shows owning graph node + traced requirements
 * - Requirement selected: shows implementing files
 * - Nothing selected: overview stats + LLR legend
 */

import {
  FileCode, FlaskConical, AlertTriangle, CheckCircle2,
  Gauge, ChevronRight, XCircle, Loader2,
} from 'lucide-react';
import type { GNode } from '@/components/NodeTablePanel';
import type { TracedFile } from './types';
import type { LineTrace } from './CodeTraceView';
import { useStore, type LogEntry } from '@/store';
import { useMemo } from 'react';

// ── Helpers (from old CodeGenPanel) ────────────────────────────────────────

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function filesForRequirement(files: TracedFile[], reqId: string): TracedFile[] {
  return files.filter(f =>
    f.lineTraces.some(t =>
      t.llr_ids.includes(reqId) || t.case_ids?.includes(reqId),
    ),
  );
}

function tracesForRequirement(file: TracedFile, reqId: string): LineTrace[] {
  return file.lineTraces.filter(t =>
    t.llr_ids.includes(reqId) || t.case_ids?.includes(reqId),
  );
}

// ── Status parsing (from old CodeGenPanel) ─────────────────────────────────

interface GenStatus {
  phase: 'idle' | 'generating' | 'fixing' | 'tidying' | 'done';
  step: string;
  coveragePct: number | null;
  testsPassed: boolean | null;
}

function parseStatus(logs: LogEntry[]): GenStatus {
  const s: GenStatus = { phase: 'idle', step: '', coveragePct: null, testsPassed: null };
  for (const log of logs.filter(l => l.cat.trim() === 'CGEN')) {
    const m = log.msg;
    if (m.includes('Phase 12 Code Gen started')) { s.phase = 'generating'; s.step = 'Starting'; }
    if (m.includes('Step 1:')) s.step = m.replace(/.*Step 1:\s*/, 'Gen: ');
    if (m.includes('Step 2:')) s.step = m.replace(/.*Step 2:\s*/, 'Gen: ');
    const ok = m.match(/\[\d+\/\d+\] OK → (\S+)/);
    if (ok) s.step = ok[1];
    if (m.includes('Step 3:')) { s.phase = 'fixing'; s.step = 'Fix loop'; }
    const fr = m.match(/Fix loop round (\d+)\/(\d+)/);
    if (fr) s.step = `Fix ${fr[1]}/${fr[2]}`;
    const tr = m.match(/Test result: passed=(\w+) coverage=(\d+)%/);
    if (tr) { s.testsPassed = tr[1] === 'True'; s.coveragePct = +tr[2]; }
    if (m.includes('Step 4:')) { s.phase = 'tidying'; s.step = 'Tidying'; }
    if (m.includes('Step 6:')) s.step = 'Trace audit';
    if (m.includes('Phase 12 complete')) { s.phase = 'done'; s.step = 'Complete'; }
  }
  return s;
}

// ── File Mode ──────────────────────────────────────────────────────────────

function FileMode({ file, ownerNode, allNodes, colorMap, onLlrHighlight }: {
  file: TracedFile;
  ownerNode: GNode | null;
  allNodes: GNode[];
  colorMap: Map<string, string>;
  onLlrHighlight: (llrId: string) => void;
}) {
  // Collect all unique LLR IDs from this file's traces
  const llrIds = useMemo(() => {
    const ids = new Set<string>();
    for (const t of file.lineTraces) t.llr_ids.forEach(id => ids.add(id));
    return Array.from(ids).sort();
  }, [file.lineTraces]);

  // Resolve LLR nodes for titles
  const llrNodes = useMemo(() => {
    const map = new Map<string, GNode>();
    for (const n of allNodes) {
      if (llrIds.includes(n.node_id)) map.set(n.node_id, n);
    }
    return map;
  }, [allNodes, llrIds]);

  const cov = file.traceCoverage;
  const pct = cov && cov.total > 0 ? Math.round((cov.traced / cov.total) * 100) : null;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Owner node card */}
      {ownerNode && (
        <div className="px-4 py-3 border-b border-forge-border/50 bg-forge-bg/30 shrink-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border border-forge-accent/30 text-forge-accent bg-forge-accent/10">
              {ownerNode.node_type}
            </span>
            <span className="text-[10px] font-mono text-forge-muted">{ownerNode.node_id}</span>
          </div>
          <p className="text-xs font-mono font-bold text-forge-text">{ownerNode.title}</p>
          {ownerNode.content && (
            <p className="text-[10px] font-mono text-forge-muted mt-1 line-clamp-2">
              {ownerNode.content.slice(0, 200)}
            </p>
          )}
        </div>
      )}

      {/* Coverage */}
      {pct !== null && (
        <div className="px-4 py-2 border-b border-forge-border/30 flex items-center gap-2 text-[10px] font-mono shrink-0">
          <Gauge size={10} className={pct >= 70 ? 'text-green-400' : 'text-amber-400'} />
          <span className={pct >= 70 ? 'text-green-400' : 'text-amber-400'}>
            {pct}% coverage
          </span>
          <span className="text-forge-muted">({cov!.traced}/{cov!.total} functions)</span>
        </div>
      )}

      {/* Traced requirements */}
      <div className="px-4 py-2 shrink-0">
        <h4 className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-2">
          Traced Requirements ({llrIds.length})
        </h4>
      </div>
      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {llrIds.length === 0 ? (
          <p className="text-[10px] font-mono text-forge-muted/50 italic">
            No LLR traces found in this file.
          </p>
        ) : (
          <div className="space-y-1">
            {llrIds.map(id => {
              const node = llrNodes.get(id);
              const color = colorMap.get(id) ?? '#60a5fa';
              const traces = file.lineTraces.filter(t => t.llr_ids.includes(id));
              const symbols = traces.map(t => t.symbol).filter(Boolean);

              return (
                <button
                  key={id}
                  onClick={() => onLlrHighlight(id)}
                  className="w-full flex items-start gap-2 p-2 rounded-lg hover:bg-forge-border/20 transition-colors text-left"
                >
                  <span
                    className="w-1 h-8 rounded-full shrink-0 mt-0.5"
                    style={{ backgroundColor: color }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span
                        className="text-[10px] font-mono font-bold px-1 rounded"
                        style={{ color, backgroundColor: hexToRgba(color, 0.12) }}
                      >
                        {id}
                      </span>
                    </div>
                    {node && (
                      <p className="text-[10px] font-mono text-forge-muted mt-0.5 truncate">
                        {node.title}
                      </p>
                    )}
                    {symbols.length > 0 && (
                      <p className="text-[9px] font-mono text-forge-muted/60 mt-0.5">
                        {symbols.join(', ')}
                      </p>
                    )}
                  </div>
                  <ChevronRight size={10} className="text-forge-muted/40 shrink-0 mt-1" />
                </button>
              );
            })}
          </div>
        )}

        {/* Untraced functions */}
        {file.untracedFunctions.length > 0 && (
          <div className="mt-4">
            <h4 className="text-[9px] font-mono text-red-400/80 uppercase tracking-wider mb-2 flex items-center gap-1">
              <AlertTriangle size={9} />
              Untraced Functions ({file.untracedFunctions.length})
            </h4>
            {file.untracedFunctions.map(u => (
              <div key={`${u.name}-${u.start}`} className="flex items-center gap-2 py-1 text-[10px] font-mono">
                <span className="text-red-400/60">{u.is_private ? '(private)' : '⚠'}</span>
                <span className="text-forge-text/70">{u.name}</span>
                <span className="text-forge-muted/40 ml-auto">L{u.start}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Requirement Mode ───────────────────────────────────────────────────────

function RequirementMode({ reqNode, tracedFiles, colorMap, onFileSelect }: {
  reqNode: GNode;
  tracedFiles: TracedFile[];
  colorMap: Map<string, string>;
  onFileSelect: (path: string) => void;
}) {
  const reqId = reqNode.node_id;
  const matchingFiles = filesForRequirement(tracedFiles, reqId);
  const sourceFiles = matchingFiles.filter(f => f.filePath.startsWith('src/'));
  const testFiles = matchingFiles.filter(f => f.filePath.startsWith('tests/'));

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Requirement header */}
      <div className="px-4 py-3 border-b border-forge-border/50 bg-forge-bg/30 shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border border-forge-accent/30 text-forge-accent bg-forge-accent/10">
            {reqNode.node_type}
          </span>
          <span className="text-[10px] font-mono text-forge-muted">{reqId}</span>
        </div>
        <p className="text-xs font-mono font-bold text-forge-text">{reqNode.title}</p>
        {reqNode.content && (
          <p className="text-[10px] font-mono text-forge-muted mt-1 line-clamp-3">
            {reqNode.content.slice(0, 300)}
          </p>
        )}
        {reqNode.trace_to.length > 0 && (
          <p className="text-[9px] font-mono text-forge-muted/60 mt-1">
            Traced from: {reqNode.trace_to.join(', ')}
          </p>
        )}
      </div>

      {/* Implementing files */}
      <div className="flex-1 overflow-y-auto p-4">
        {matchingFiles.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertTriangle size={20} className="text-amber-400/40 mb-2" />
            <p className="text-xs font-mono text-forge-muted">No code traces for {reqId}</p>
            <p className="text-[10px] font-mono text-forge-muted/60 mt-1">
              Run Phase 12 to generate implementations.
            </p>
          </div>
        ) : (
          <>
            <FileGroupList
              label="Source Implementation"
              icon={<FileCode size={11} className="text-blue-400" />}
              files={sourceFiles}
              reqId={reqId}
              colorMap={colorMap}
              onFileSelect={onFileSelect}
            />
            <FileGroupList
              label="Test Coverage"
              icon={<FlaskConical size={11} className="text-green-400" />}
              files={testFiles}
              reqId={reqId}
              colorMap={colorMap}
              onFileSelect={onFileSelect}
            />
          </>
        )}
      </div>
    </div>
  );
}

function FileGroupList({ label, icon, files, reqId, colorMap, onFileSelect }: {
  label: string;
  icon: React.ReactNode;
  files: TracedFile[];
  reqId: string;
  colorMap: Map<string, string>;
  onFileSelect: (path: string) => void;
}) {
  if (files.length === 0) return null;
  const color = colorMap.get(reqId) ?? '#60a5fa';

  return (
    <div className="mb-4">
      <h4 className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-2 flex items-center gap-1">
        {icon} {label}
      </h4>
      <div className="space-y-1">
        {files.map(f => {
          const traces = tracesForRequirement(f, reqId);
          const symbols = traces.map(t => t.symbol).filter(Boolean);
          const lineRange = traces.length > 0
            ? `L${Math.min(...traces.map(t => t.start))}-${Math.max(...traces.map(t => t.end))}`
            : '';

          return (
            <button
              key={f.filePath}
              onClick={() => onFileSelect(f.filePath)}
              className="w-full flex items-start gap-2 p-2 rounded-lg hover:bg-forge-border/20 transition-colors text-left"
            >
              <span
                className="w-1 h-6 rounded-full shrink-0 mt-0.5"
                style={{ backgroundColor: color }}
              />
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-mono text-forge-text truncate">
                  {f.filePath}
                </p>
                <p className="text-[9px] font-mono text-forge-muted/60 mt-0.5">
                  {symbols.join(', ')} {lineRange && `· ${lineRange}`}
                </p>
              </div>
              <ChevronRight size={10} className="text-forge-muted/40 shrink-0 mt-1" />
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Overview Mode ──────────────────────────────────────────────────────────

function OverviewMode({ tracedFiles }: {
  tracedFiles: TracedFile[];
}) {
  const logs = useStore(s => s.logs);
  const status = useMemo(() => parseStatus(logs), [logs]);

  const totalFuncs = tracedFiles.reduce((s, f) => s + (f.traceCoverage?.total ?? 0), 0);
  const tracedFuncs = tracedFiles.reduce((s, f) => s + (f.traceCoverage?.traced ?? 0), 0);
  const totalGaps = tracedFiles.reduce((s, f) => s + f.untracedFunctions.length, 0);
  const sourceFiles = tracedFiles.filter(f => f.filePath.startsWith('src/'));
  const testFiles = tracedFiles.filter(f => f.filePath.startsWith('tests/'));
  const isActive = status.phase !== 'idle' && status.phase !== 'done';

  return (
    <div className="flex flex-col h-full overflow-y-auto p-4">
      {/* Status */}
      <div className="flex items-center gap-2 mb-4 text-xs font-mono">
        {isActive ? (
          <Loader2 size={12} className="text-emerald-400 animate-spin" />
        ) : status.phase === 'done' ? (
          <CheckCircle2 size={12} className="text-green-400" />
        ) : null}
        <span className="text-forge-muted">{status.step || 'Phase 12 — Code Generation'}</span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2 mb-4">
        <StatCard label="Source" value={`${sourceFiles.length}`} sub="files" />
        <StatCard label="Tests" value={`${testFiles.length}`} sub="files" />
        <StatCard
          label="Functions"
          value={`${tracedFuncs}/${totalFuncs}`}
          sub="traced"
          accent={totalGaps > 0 ? 'red' : 'green'}
        />
        <StatCard
          label="Coverage"
          value={status.coveragePct !== null ? `${status.coveragePct}%` : '—'}
          sub="statement"
          accent={
            status.coveragePct !== null
              ? status.coveragePct >= 70 ? 'green' : 'amber'
              : undefined
          }
        />
      </div>

      {/* Test status */}
      {status.testsPassed !== null && (
        <div className="flex items-center gap-2 mb-4 text-xs font-mono">
          {status.testsPassed
            ? <><CheckCircle2 size={11} className="text-green-400" /> <span className="text-green-400">All tests passing</span></>
            : <><XCircle size={11} className="text-red-400" /> <span className="text-red-400">Test failures</span></>}
        </div>
      )}

      {/* Gaps warning */}
      {totalGaps > 0 && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-lg bg-red-400/5 border border-red-400/20 text-xs font-mono text-red-400">
          <AlertTriangle size={11} />
          {totalGaps} untraced function{totalGaps !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, accent }: {
  label: string;
  value: string;
  sub: string;
  accent?: 'green' | 'amber' | 'red';
}) {
  const accentCls = accent === 'green' ? 'text-green-400'
    : accent === 'amber' ? 'text-amber-400'
    : accent === 'red' ? 'text-red-400'
    : 'text-forge-text';

  return (
    <div className="bg-forge-bg/50 rounded-lg border border-forge-border/50 p-2 text-center">
      <div className={`text-sm font-bold font-mono ${accentCls}`}>{value}</div>
      <div className="text-[8px] font-mono text-forge-muted uppercase">{label} · {sub}</div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface TraceMatrixPanelProps {
  mode: 'file' | 'requirement' | 'overview';
  selectedFile: TracedFile | null;
  ownerNode: GNode | null;
  selectedReq: GNode | null;
  allNodes: GNode[];
  tracedFiles: TracedFile[];
  colorMap: Map<string, string>;
  onFileSelect: (path: string) => void;
  onLlrHighlight: (llrId: string) => void;
}

export default function TraceMatrixPanel({
  mode, selectedFile, ownerNode, selectedReq,
  allNodes, tracedFiles, colorMap, onFileSelect, onLlrHighlight,
}: TraceMatrixPanelProps) {
  return (
    <div className="flex flex-col h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
      <div className="px-3 py-2 border-b border-forge-border/50 shrink-0">
        <h3 className="text-[10px] font-mono font-bold text-forge-muted uppercase tracking-wider">
          {mode === 'file' ? 'File Traceability'
            : mode === 'requirement' ? 'Requirement Traceability'
            : 'Overview'}
        </h3>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {mode === 'file' && selectedFile ? (
          <FileMode
            file={selectedFile}
            ownerNode={ownerNode}
            allNodes={allNodes}
            colorMap={colorMap}
            onLlrHighlight={onLlrHighlight}
          />
        ) : mode === 'requirement' && selectedReq ? (
          <RequirementMode
            reqNode={selectedReq}
            tracedFiles={tracedFiles}
            colorMap={colorMap}
            onFileSelect={onFileSelect}
          />
        ) : (
          <OverviewMode tracedFiles={tracedFiles} />
        )}
      </div>
    </div>
  );
}
