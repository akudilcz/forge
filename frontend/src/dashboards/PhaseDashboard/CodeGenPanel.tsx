/**
 * CodeGenPanel — Phase 12 two-column traceability dashboard.
 *
 * Left:  Enriched WorkspaceTreePanel (files → functions → traced nodes)
 * Right: CodeTraceView with breadcrumb bar
 *
 * Trace colours use the standard node-type chip colours (TYPE_HEX),
 * so LLR traces are orange, CASE traces are green, HLR traces are amber, etc.
 */

import { useState, useMemo, useCallback } from 'react';
import type { GNode } from '@/components/NodeTablePanel';
import { ResizableSplit } from '@/components/ResizableSplit';
import { TYPE_HEX } from '@/lib/nodeColors';
import { useStore } from '@/store';
import type { TracedFile } from './types';
import type { LineTrace, SuggestedTrace, UntracedFunction } from './CodeTraceView';
import CodeTraceView from './CodeTraceView';
import WorkspaceTreePanel from './WorkspaceTreePanel';

// ── Colour map by node type ──────────────────────────────────────────────────

function buildNodeColorMap(nodes: GNode[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const n of nodes) {
    const hex = TYPE_HEX[n.node_type] ?? '#94a3b8';
    map.set(n.node_id, hex);
  }
  return map;
}

// ── Data extraction ──────────────────────────────────────────────────────────

function extractTracedFiles(nodes: GNode[]): TracedFile[] {
  return nodes
    .filter(n => n.properties?.file_path && n.properties?.line_traces)
    .map(n => {
      const audit = n.properties.trace_audit as Record<string, unknown> | undefined;
      return {
        nodeId: n.node_id,
        filePath: n.properties.file_path as string,
        lineTraces: (n.properties.line_traces as LineTrace[]) || [],
        suggestedTraces: (audit?.suggested_traces as SuggestedTrace[]) || [],
        untracedFunctions: (n.properties.untraced_functions as UntracedFunction[]) || [],
        traceCoverage: (n.properties.trace_coverage as { total: number; traced: number }) ?? null,
      };
    })
    .sort((a, b) => a.filePath.localeCompare(b.filePath));
}

// ── Breadcrumb bar ───────────────────────────────────────────────────────────

function Breadcrumbs({ filePath, ownerNode, llrIds, colorMap, onLlrClick }: {
  filePath: string;
  ownerNode: GNode | null;
  llrIds: string[];
  colorMap: Map<string, string>;
  onLlrClick: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-forge-border/30 bg-forge-bg/20 text-[10px] font-mono shrink-0 overflow-x-auto">
      <span className="text-forge-text/80">{filePath}</span>
      {ownerNode && (
        <>
          <span className="text-forge-muted/40">→</span>
          <span className="text-forge-accent/70">{ownerNode.node_id}</span>
        </>
      )}
      {llrIds.length > 0 && (
        <>
          <span className="text-forge-muted/40">→</span>
          {llrIds.slice(0, 5).map(id => {
            const color = colorMap.get(id) ?? '#94a3b8';
            return (
              <button
                key={id}
                onClick={() => onLlrClick(id)}
                className="px-1 rounded hover:underline cursor-pointer"
                style={{ color, backgroundColor: `${color}1a` }}
              >
                {id}
              </button>
            );
          })}
          {llrIds.length > 5 && (
            <span className="text-forge-muted/50">+{llrIds.length - 5}</span>
          )}
        </>
      )}
    </div>
  );
}

// ── Empty state for code view ────────────────────────────────────────────────

function EmptyCodeView({ hasFiles }: { hasFiles: boolean }) {
  return (
    <div className="h-full flex items-center justify-center text-forge-muted text-xs font-mono">
      {hasFiles
        ? 'Select a file or function to view code.'
        : 'Run Phase 12 to generate code and tests.'}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export default function CodeGenPanel({ nodes, selectedRequirementIds }: {
  nodes: GNode[];
  selectedRequirementIds?: Set<string>;
}) {
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [scrollToLine, setScrollToLine] = useState<number | null>(null);
  const [scrollToLineEnd, setScrollToLineEnd] = useState<number | null>(null);
  const [highlightLlr, setHighlightLlr] = useState<string | null>(null);

  const traced = useMemo(() => extractTracedFiles(nodes), [nodes]);
  const colorMap = useMemo(() => buildNodeColorMap(nodes), [nodes]);

  // Derived state
  const selectedFile = traced.find(f => f.filePath === selectedFilePath) ?? null;
  const ownerNode = selectedFile
    ? nodes.find(n => n.properties?.file_path === selectedFilePath) ?? null
    : null;

  const fileLlrIds = useMemo(() => {
    if (!selectedFile) return [];
    const ids = new Set<string>();
    for (const t of selectedFile.lineTraces) t.llr_ids.forEach(id => ids.add(id));
    return Array.from(ids).sort();
  }, [selectedFile]);

  const logAction = useStore((s) => s.logUserAction);
  const handleFileSelect = useCallback((path: string) => {
    logAction(`Select file: ${path}`);
    setSelectedFilePath(path);
    setScrollToLine(null);
    setScrollToLineEnd(null);
    setHighlightLlr(null);
  }, [logAction]);

  const handleFunctionSelect = useCallback((path: string, line: number, lineEnd?: number) => {
    logAction(`Select function: ${path}:${line}`);
    setSelectedFilePath(path);
    setScrollToLine(line);
    setScrollToLineEnd(lineEnd ?? null);
    setHighlightLlr(null);
  }, [logAction]);

  const handleLlrHighlight = useCallback((llrId: string) => {
    setHighlightLlr(prev => prev === llrId ? null : llrId);
  }, []);

  return (
    <ResizableSplit
      initialSplit={30}
      minLeft={20}
      maxLeft={45}
      storageKey="phase-12-inner"
      left={
        <div className="h-full pr-1">
          <WorkspaceTreePanel
            tracedFiles={traced}
            allNodes={nodes}
            selectedFile={selectedFilePath}
            onFileSelect={handleFileSelect}
            onFunctionSelect={handleFunctionSelect}
            onLlrHighlight={handleLlrHighlight}
            selectedRequirementIds={selectedRequirementIds}
          />
        </div>
      }
      right={
        <div className="h-full pl-1">
          {selectedFilePath ? (
            <div className="flex flex-col h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
              <Breadcrumbs
                filePath={selectedFilePath}
                ownerNode={ownerNode}
                llrIds={fileLlrIds}
                colorMap={colorMap}
                onLlrClick={handleLlrHighlight}
              />
              <div className="flex-1 min-h-0">
                <CodeTraceView
                  filePath={selectedFilePath}
                  lineTraces={selectedFile?.lineTraces ?? []}
                  suggestedTraces={selectedFile?.suggestedTraces ?? []}
                  untracedFunctions={selectedFile?.untracedFunctions ?? []}
                  highlightLlr={highlightLlr}
                  onLlrClick={handleLlrHighlight}
                  llrColorMap={colorMap}
                  scrollToLine={scrollToLine}
                  scrollToLineEnd={scrollToLineEnd}
                  selectedRequirementIds={selectedRequirementIds}
                />
              </div>
            </div>
          ) : (
            <div className="h-full bg-forge-surface rounded-xl border border-forge-border overflow-hidden">
              <EmptyCodeView hasFiles={traced.length > 0} />
            </div>
          )}
        </div>
      }
    />
  );
}
