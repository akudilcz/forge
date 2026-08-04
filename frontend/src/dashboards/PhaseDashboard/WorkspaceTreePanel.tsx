/**
 * WorkspaceTreePanel — Enriched source tree for Phase 12 dashboard.
 *
 * Three-level hierarchy:
 *   1. Directories & files (from workspace tree API + trace data)
 *   2. Functions within each file (from lineTraces + untracedFunctions)
 *   3. Traced graph nodes per function (LLR, CASE, etc.)
 *
 * Clicking a file or function navigates the CodeTraceView.
 * The tree auto-refreshes every 5s so new files appear during generation.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useStore } from '@/store';
import {
  ChevronRight, ChevronDown, FileCode, FlaskConical,
  FolderOpen, Folder, AlertTriangle, Box,
} from 'lucide-react';
import { TYPE_HEX } from '@/lib/nodeColors';
import type { GNode } from '@/components/NodeTablePanel';
import type { TracedFile } from './types';
import {
  type FunctionEntry, type ResultStatus,
  buildCaseResultMap, testFuncStatus, extractFunctions, fileLevelStatus,
} from './traceStatus';

// ── Props ──────────────────────────────────────────────────────────────────

interface WorkspaceTreePanelProps {
  tracedFiles: TracedFile[];
  allNodes: GNode[];
  selectedFile: string | null;
  onFileSelect: (path: string) => void;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (llrId: string) => void;
  /** When non-empty, dims files/functions that don't trace to any selected requirement. */
  selectedRequirementIds?: Set<string>;
}

// ── Selection-match helpers ─────────────────────────────────────────────

function setsIntersect(a: Set<string> | string[], b: Set<string>): boolean {
  if (Array.isArray(a)) return a.some(id => b.has(id));
  for (const id of a) if (b.has(id)) return true;
  return false;
}

function fileMatchesSelection(traced: TracedFile | undefined, sel: Set<string>): boolean {
  if (!traced) return false;
  return traced.lineTraces.some(t => setsIntersect(t.llr_ids, sel));
}

function functionMatchesSelection(func: FunctionEntry, sel: Set<string>): boolean {
  return setsIntersect(func.llrIds, sel);
}

function treeNodeMatchesSelection(node: TreeNode, sel: Set<string>): boolean {
  if (!node.isDir) return fileMatchesSelection(node.traced, sel);
  return node.children.some(c => treeNodeMatchesSelection(c, sel));
}

// ── Tree types ─────────────────────────────────────────────────────────────

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children: TreeNode[];
  traced?: TracedFile;
}

// ── Build tree from flat workspace listing ─────────────────────────────────

interface WsEntry {
  name: string;
  type: 'file' | 'directory';
  children?: WsEntry[];
}

function buildTree(
  entries: WsEntry[],
  parentPath: string,
  traceMap: Map<string, TracedFile>,
): TreeNode[] {
  const nodes: TreeNode[] = [];
  for (const e of entries) {
    // Skip __init__.py — empty package markers with no functions
    if (e.name === '__init__.py') continue;
    const path = parentPath ? `${parentPath}/${e.name}` : e.name;
    nodes.push({
      name: e.name,
      path,
      isDir: e.type === 'directory',
      children: e.children ? buildTree(e.children, path, traceMap) : [],
      traced: traceMap.get(path),
    });
  }
  nodes.sort((a, b) => {
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return nodes;
}

// ── Coverage badge ─────────────────────────────────────────────────────────

function CoverageDot({ status }: { status: 'green' | 'amber' | 'red' | 'grey' }) {
  const cls = status === 'green' ? 'bg-green-400'
    : status === 'amber' ? 'bg-amber-400'
    : status === 'red' ? 'bg-red-400'
    : 'bg-slate-500/40';
  const title = status === 'grey' ? 'No graph node' : `${status}`;
  return <span className={`w-2 h-2 rounded-full shrink-0 ${cls}`} title={title} />;
}

function CoverageLabel({ traced }: { traced?: TracedFile }) {
  if (!traced?.traceCoverage || traced.traceCoverage.total === 0) return null;
  const { traced: t, total } = traced.traceCoverage;
  const pct = Math.round((t / total) * 100);
  return (
    <span className="text-[9px] font-mono text-forge-muted/60 shrink-0 ml-auto">
      {pct}%
    </span>
  );
}

// ── Traced node leaf ───────────────────────────────────────────────────────

function TracedNodeLeaf({ nodeId, allNodes, onLlrHighlight, depth }: {
  nodeId: string;
  allNodes: GNode[];
  onLlrHighlight: (id: string) => void;
  depth: number;
}) {
  const node = allNodes.find(n => n.node_id === nodeId);
  const prefix = nodeId.replace(/-\d+$/, '');
  const color = TYPE_HEX[prefix] ?? '#94a3b8';

  return (
    <button
      onClick={() => onLlrHighlight(nodeId)}
      className="w-full flex items-center gap-1.5 py-0.5 pr-2 text-left hover:bg-forge-border/10 transition-colors"
      style={{ paddingLeft: `${depth * 12 + 8}px` }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ backgroundColor: color }}
      />
      <span
        className="text-[9px] font-mono font-bold px-1 rounded truncate"
        style={{ color, backgroundColor: `${color}1a` }}
      >
        {nodeId}
      </span>
      {node && (
        <span className="text-[9px] font-mono text-forge-muted/50 truncate">
          {node.title}
        </span>
      )}
    </button>
  );
}

// ── Function row ───────────────────────────────────────────────────────────

function FunctionRow({ func, filePath, depth, allNodes, isTestFile, caseResultMap, onFunctionSelect, onLlrHighlight, selectedRequirementIds }: {
  func: FunctionEntry;
  filePath: string;
  depth: number;
  allNodes: GNode[];
  isTestFile: boolean;
  caseResultMap: Map<string, ResultStatus>;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (id: string) => void;
  selectedRequirementIds?: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const hasChildren = func.llrIds.length > 0 || func.caseIds.length > 0;

  // Determine dot colour
  const dotColor = isTestFile
    ? testFuncStatus(func, caseResultMap)
    : func.isTraced ? 'green' : 'red';
  const dotCls = dotColor === 'green' ? 'bg-green-400'
    : dotColor === 'amber' ? 'bg-amber-400'
    : 'bg-red-400';

  const isDimmed = selectedRequirementIds && selectedRequirementIds.size > 0
    && !functionMatchesSelection(func, selectedRequirementIds);

  return (
    <>
      <div
        className={`w-full flex items-center gap-1.5 py-0.5 pr-2 hover:bg-forge-border/10 transition-colors ${isDimmed ? 'opacity-30' : ''}`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {/* Expand toggle (only if has traced nodes) */}
        {hasChildren ? (
          <button onClick={() => setOpen(!open)} className="shrink-0">
            {open
              ? <ChevronDown size={9} className="text-forge-muted/60" />
              : <ChevronRight size={9} className="text-forge-muted/60" />}
          </button>
        ) : (
          <span className="w-[9px] shrink-0" />
        )}

        {/* Function dot */}
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}`} />

        {/* Clickable function name — navigates to code */}
        <button
          onClick={() => onFunctionSelect(filePath, func.startLine, func.endLine)}
          className="text-[10px] font-mono text-forge-text/70 hover:text-forge-text truncate text-left"
          title={`${func.name} — L${func.startLine}`}
        >
          <span className="text-forge-muted/50">ƒ</span> {func.name}
        </button>

        {dotColor === 'red' && (
          <AlertTriangle size={8} className="text-red-400 shrink-0" />
        )}

        <span className="text-[8px] font-mono text-forge-muted/40 shrink-0 ml-auto">
          L{func.startLine}
        </span>
      </div>

      {/* Level 3: Traced node IDs + CASE IDs */}
      {open && [...func.llrIds, ...func.caseIds].map(id => (
        <TracedNodeLeaf
          key={id}
          nodeId={id}
          allNodes={allNodes}
          onLlrHighlight={onLlrHighlight}
          depth={depth + 1}
        />
      ))}
    </>
  );
}

// ── Class grouping row ──────────────────────────────────────────────────────

function ClassGroupRow({ className, functions, filePath, depth, allNodes, isTestFile, caseResultMap, onFunctionSelect, onLlrHighlight, selectedRequirementIds }: {
  className: string;
  functions: FunctionEntry[];
  filePath: string;
  depth: number;
  allNodes: GNode[];
  isTestFile: boolean;
  caseResultMap: Map<string, ResultStatus>;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (id: string) => void;
  selectedRequirementIds?: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const classStatus = fileLevelStatus(functions, isTestFile, caseResultMap);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1.5 py-0.5 pr-2 hover:bg-forge-border/10 transition-colors"
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {open
          ? <ChevronDown size={9} className="text-forge-muted/60 shrink-0" />
          : <ChevronRight size={9} className="text-forge-muted/60 shrink-0" />}
        <CoverageDot status={classStatus} />
        <Box size={9} className="text-purple-400/70 shrink-0" />
        <span className="text-[10px] font-mono text-purple-400/80 truncate">
          {className}
        </span>
        {classStatus === 'red' && <AlertTriangle size={8} className="text-red-400 shrink-0" />}
        <span className="text-[8px] font-mono text-forge-muted/40 shrink-0 ml-auto">
          {functions.filter(f => f.isTraced).length}/{functions.length}
        </span>
      </button>
      {open && functions.map(func => (
        <FunctionRow
          key={`${func.name}-${func.startLine}`}
          func={func}
          filePath={filePath}
          depth={depth + 1}
          allNodes={allNodes}
          isTestFile={isTestFile}
          caseResultMap={caseResultMap}
          onFunctionSelect={onFunctionSelect}
          onLlrHighlight={onLlrHighlight}
          selectedRequirementIds={selectedRequirementIds}
        />
      ))}
    </>
  );
}

// ── File row (expandable to show functions) ────────────────────────────────

function FileRow({ node, depth, selectedFile, allNodes, caseResultMap, onFileSelect, onFunctionSelect, onLlrHighlight, selectedRequirementIds }: {
  node: TreeNode;
  depth: number;
  selectedFile: string | null;
  allNodes: GNode[];
  caseResultMap: Map<string, ResultStatus>;
  onFileSelect: (path: string) => void;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (id: string) => void;
  selectedRequirementIds?: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const isTest = node.path.startsWith('tests/');
  const Icon = isTest ? FlaskConical : FileCode;
  const iconCls = isTest ? 'text-green-400/70' : 'text-blue-400/70';
  const isSelected = selectedFile === node.path;
  const recentEdits = useStore(s => s.recentlyEditedFiles);
  const isRecentlyEdited = (recentEdits[node.path] ?? 0) > Date.now();

  const functions = useMemo(
    () => node.traced ? extractFunctions(node.traced) : [],
    [node.traced],
  );
  const hasFunctions = functions.length > 0;
  const fileStatus = node.traced
    ? fileLevelStatus(functions, isTest, caseResultMap)
    : 'grey' as const;

  const isDimmed = selectedRequirementIds && selectedRequirementIds.size > 0
    && !fileMatchesSelection(node.traced, selectedRequirementIds);

  return (
    <>
      <div
        className={`w-full flex items-center gap-1.5 py-1 pr-2 transition-all duration-500 ${
          isSelected
            ? 'bg-forge-border/40 text-forge-text'
            : 'hover:bg-forge-border/10 text-forge-text/70'
        } ${isDimmed ? 'opacity-30' : ''} ${
          isRecentlyEdited ? 'bg-cyan-400/15 ring-1 ring-cyan-400/30' : ''
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {/* Expand toggle */}
        {hasFunctions ? (
          <button onClick={() => setOpen(!open)} className="shrink-0">
            {open
              ? <ChevronDown size={10} className="text-forge-muted/60" />
              : <ChevronRight size={10} className="text-forge-muted/60" />}
          </button>
        ) : (
          <span className="w-[10px] shrink-0" />
        )}

        <CoverageDot status={fileStatus} />

        {/* Clickable file name — navigates to code */}
        <button
          onClick={() => onFileSelect(node.path)}
          className="flex items-center gap-1.5 min-w-0 text-left"
        >
          <Icon size={11} className={`${iconCls} shrink-0`} />
          <span className={`text-[11px] font-mono truncate ${node.traced ? '' : 'text-forge-muted/50'}`}>
            {node.name}
          </span>
        </button>

        {fileStatus === 'red' && <AlertTriangle size={9} className="text-red-400 shrink-0" />}
        {!isTest && <CoverageLabel traced={node.traced} />}
      </div>

      {/* Level 2: Classes and Functions */}
      {open && (() => {
        // Group functions by class — module-level functions have className=''
        const byClass = new Map<string, FunctionEntry[]>();
        for (const func of functions) {
          const cls = func.className || '';
          const list = byClass.get(cls) ?? [];
          list.push(func);
          byClass.set(cls, list);
        }
        // Sort: classes first (alphabetically), then module-level functions
        const classNames = [...byClass.keys()].sort((a, b) => {
          if (!a) return 1;  // module-level goes last
          if (!b) return -1;
          return a.localeCompare(b);
        });
        return classNames.map(cls => {
          const classFuncs = byClass.get(cls)!;
          if (cls) {
            // Class group with nested functions
            return (
              <ClassGroupRow
                key={`class-${cls}`}
                className={cls}
                functions={classFuncs}
                filePath={node.path}
                depth={depth + 1}
                allNodes={allNodes}
                isTestFile={isTest}
                caseResultMap={caseResultMap}
                onFunctionSelect={onFunctionSelect}
                onLlrHighlight={onLlrHighlight}
                selectedRequirementIds={selectedRequirementIds}
              />
            );
          }
          // Module-level functions (no class)
          return classFuncs.map(func => (
            <FunctionRow
              key={`${func.name}-${func.startLine}`}
              func={func}
              filePath={node.path}
              depth={depth + 1}
              allNodes={allNodes}
              isTestFile={isTest}
              caseResultMap={caseResultMap}
              onFunctionSelect={onFunctionSelect}
              onLlrHighlight={onLlrHighlight}
              selectedRequirementIds={selectedRequirementIds}
            />
          ));
        });
      })()}
    </>
  );
}

// ── Directory row ──────────────────────────────────────────────────────────

function DirRow({ node, depth, selectedFile, allNodes, caseResultMap, onFileSelect, onFunctionSelect, onLlrHighlight, defaultOpen, selectedRequirementIds }: {
  node: TreeNode;
  depth: number;
  selectedFile: string | null;
  allNodes: GNode[];
  caseResultMap: Map<string, ResultStatus>;
  onFileSelect: (path: string) => void;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (id: string) => void;
  defaultOpen: boolean;
  selectedRequirementIds?: Set<string>;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const fileCount = countFiles(node);
  const tracedCount = countTracedFiles(node);

  const isDimmed = selectedRequirementIds && selectedRequirementIds.size > 0
    && !treeNodeMatchesSelection(node, selectedRequirementIds);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        className={`w-full flex items-center gap-1.5 py-1 pr-2 text-left hover:bg-forge-border/10 transition-colors ${isDimmed ? 'opacity-30' : ''}`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {open
          ? <ChevronDown size={10} className="text-forge-muted/60 shrink-0" />
          : <ChevronRight size={10} className="text-forge-muted/60 shrink-0" />}
        {open
          ? <FolderOpen size={11} className="text-amber-400/70 shrink-0" />
          : <Folder size={11} className="text-amber-400/50 shrink-0" />}
        <span className="text-[11px] font-mono text-forge-text/80 truncate">
          {node.name}/
        </span>
        <span className="text-[9px] font-mono text-forge-muted/50 shrink-0 ml-auto">
          {tracedCount}/{fileCount}
        </span>
      </button>
      {open && node.children.map(child => (
        <TreeRowDispatch
          key={child.path}
          node={child}
          depth={depth + 1}
          selectedFile={selectedFile}
          allNodes={allNodes}
          caseResultMap={caseResultMap}
          onFileSelect={onFileSelect}
          onFunctionSelect={onFunctionSelect}
          onLlrHighlight={onLlrHighlight}
          selectedRequirementIds={selectedRequirementIds}
        />
      ))}
    </>
  );
}

// ── Row dispatcher ─────────────────────────────────────────────────────────

function TreeRowDispatch({ node, depth, selectedFile, allNodes, caseResultMap, onFileSelect, onFunctionSelect, onLlrHighlight, selectedRequirementIds }: {
  node: TreeNode;
  depth: number;
  selectedFile: string | null;
  allNodes: GNode[];
  caseResultMap: Map<string, ResultStatus>;
  onFileSelect: (path: string) => void;
  onFunctionSelect: (path: string, line: number, lineEnd?: number) => void;
  onLlrHighlight: (id: string) => void;
  selectedRequirementIds?: Set<string>;
}) {
  if (node.isDir) {
    return (
      <DirRow
        node={node}
        depth={depth}
        selectedFile={selectedFile}
        allNodes={allNodes}
        caseResultMap={caseResultMap}
        onFileSelect={onFileSelect}
        onFunctionSelect={onFunctionSelect}
        onLlrHighlight={onLlrHighlight}
        defaultOpen
        selectedRequirementIds={selectedRequirementIds}
      />
    );
  }
  return (
    <FileRow
      node={node}
      depth={depth}
      selectedFile={selectedFile}
      allNodes={allNodes}
      caseResultMap={caseResultMap}
      onFileSelect={onFileSelect}
      onFunctionSelect={onFunctionSelect}
      onLlrHighlight={onLlrHighlight}
      selectedRequirementIds={selectedRequirementIds}
    />
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function countFiles(node: TreeNode): number {
  if (!node.isDir) return 1;
  return node.children.reduce((s, c) => s + countFiles(c), 0);
}

function countTracedFiles(node: TreeNode): number {
  if (!node.isDir) return node.traced ? 1 : 0;
  return node.children.reduce((s, c) => s + countTracedFiles(c), 0);
}

// ── Summary bar ────────────────────────────────────────────────────────────

function SummaryBar({ allTracedFiles }: { allTracedFiles: TracedFile[] }) {
  const totalFuncs = allTracedFiles.reduce((s, f) => s + (f.traceCoverage?.total ?? 0), 0);
  const tracedFuncs = allTracedFiles.reduce((s, f) => s + (f.traceCoverage?.traced ?? 0), 0);
  const gaps = allTracedFiles.reduce((s, f) => s + f.untracedFunctions.length, 0);
  const pct = totalFuncs > 0 ? Math.round((tracedFuncs / totalFuncs) * 100) : 0;

  return (
    <div className="px-3 py-2 border-b border-forge-border/50 text-[10px] font-mono text-forge-muted shrink-0">
      <div className="flex items-center gap-2">
        <span>{allTracedFiles.length} files</span>
        <span>·</span>
        <span>{tracedFuncs}/{totalFuncs} funcs</span>
        {totalFuncs > 0 && (
          <>
            <span>·</span>
            <span className={pct >= 90 ? 'text-green-400' : pct >= 70 ? 'text-amber-400' : 'text-red-400'}>
              {pct}%
            </span>
          </>
        )}
        {gaps > 0 && (
          <span className="text-red-400 flex items-center gap-0.5 ml-auto">
            <AlertTriangle size={9} />{gaps}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

// ── API function data ──────────────────────────────────────────────────────

interface ApiFunctionEntry {
  name: string;
  start: number;
  end: number;
  is_private: boolean;
  class_name: string;
}

interface ApiFileData {
  functions: ApiFunctionEntry[];
  traces: { start: number; end: number; llr_ids: string[]; symbol: string; case_ids: string[]; class_name: string }[];
  total_functions: number;
  traced_functions: number;
}

/** Merge API-parsed functions into TracedFile entries so ALL functions appear. */
function mergeApiData(
  graphTraced: Map<string, TracedFile>,
  apiData: Record<string, ApiFileData>,
): Map<string, TracedFile> {
  const merged = new Map<string, TracedFile>(graphTraced);

  for (const [path, data] of Object.entries(apiData)) {
    const existing = merged.get(path);
    // Derive untraced functions from API data: functions not in traces
    const tracedKeys = new Set(data.traces.map(t => `${t.class_name || ''}.${t.symbol}`).filter(Boolean));
    const untracedFromApi = data.functions
      .filter(f => !tracedKeys.has(`${f.class_name || ''}.${f.name}`))
      .map(f => ({ name: f.name, start: f.start, end: f.end, is_private: f.is_private, class_name: f.class_name }));

    if (existing) {
      // Merge: keep graph trace data, fill in untraced functions from API
      const existingNames = new Set([
        ...existing.lineTraces.map(t => t.symbol).filter(Boolean),
        ...existing.untracedFunctions.map(u => u.name),
      ]);
      const newUntraced = untracedFromApi.filter(u => !existingNames.has(u.name));
      merged.set(path, {
        ...existing,
        untracedFunctions: [...existing.untracedFunctions, ...newUntraced],
        traceCoverage: existing.traceCoverage ?? {
          total: data.total_functions,
          traced: data.traced_functions,
        },
      });
    } else {
      // No graph data — create synthetic TracedFile from API
      merged.set(path, {
        nodeId: '',
        filePath: path,
        lineTraces: data.traces.map(t => ({
          start: t.start, end: t.end,
          llr_ids: t.llr_ids, symbol: t.symbol,
          case_ids: t.case_ids, class_name: t.class_name,
        })),
        suggestedTraces: [],
        untracedFunctions: untracedFromApi,
        traceCoverage: { total: data.total_functions, traced: data.traced_functions },
      });
    }
  }
  return merged;
}

export default function WorkspaceTreePanel({
  tracedFiles, allNodes, selectedFile,
  onFileSelect, onFunctionSelect, onLlrHighlight,
  selectedRequirementIds,
}: WorkspaceTreePanelProps) {
  const { data: wsTree } = useQuery<{ tree: WsEntry }>({
    queryKey: ['workspace-tree'],
    queryFn: () => fetch('/api/workspace/tree?depth=3').then(r => r.json()),
    refetchInterval: 5_000,
  });

  // Fetch live-parsed functions from workspace files
  const { data: apiFunctions } = useQuery<Record<string, ApiFileData>>({
    queryKey: ['workspace-functions'],
    queryFn: () => fetch('/api/workspace/functions').then(r => r.json()),
    refetchInterval: 5_000,
  });

  const traceMap = useMemo(() => {
    const graphMap = new Map<string, TracedFile>();
    for (const f of tracedFiles) graphMap.set(f.filePath, f);
    if (apiFunctions) return mergeApiData(graphMap, apiFunctions);
    return graphMap;
  }, [tracedFiles, apiFunctions]);

  const tree = useMemo(() => {
    if (!wsTree?.tree?.children) return [];
    const relevant = wsTree.tree.children.filter(
      (c: WsEntry) => c.name === 'src' || c.name === 'tests',
    );
    return buildTree(relevant, '', traceMap);
  }, [wsTree, traceMap]);

  const caseResultMap = useMemo(() => buildCaseResultMap(allNodes), [allNodes]);

  return (
    <div className="flex flex-col h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
      <div className="px-3 py-2 border-b border-forge-border/50 shrink-0">
        <h3 className="text-[10px] font-mono font-bold text-forge-muted uppercase tracking-wider">
          Source Tree
        </h3>
      </div>
      <SummaryBar allTracedFiles={Array.from(traceMap.values())} />
      <div className="flex-1 overflow-y-auto py-1">
        {tree.length === 0 ? (
          <p className="text-[10px] font-mono text-forge-muted/50 text-center py-8 px-3">
            No source files yet — run Phase 12.
          </p>
        ) : (
          tree.map(node => (
            <DirRow
              key={node.path}
              node={node}
              depth={0}
              selectedFile={selectedFile}
              allNodes={allNodes}
              caseResultMap={caseResultMap}
              onFileSelect={onFileSelect}
              onFunctionSelect={onFunctionSelect}
              onLlrHighlight={onLlrHighlight}
              defaultOpen
              selectedRequirementIds={selectedRequirementIds}
            />
          ))
        )}
      </div>
    </div>
  );
}
