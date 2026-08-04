/**
 * CodeTraceView — safety-critical LLR traceability viewer.
 *
 * Uses Monaco editor for VS Code-like syntax highlighting and editing.
 * Trace overlays are applied via Monaco line decorations:
 * - **Confirmed** (green tint + glyph): function has a verified `# LLR:` annotation
 * - **Suggested** (amber stripe + glyph): LLM-suggested trace, pending review
 * - **Untraced** (red tint + glyph): no trace — traceability gap
 *
 * Hover over the glyph margin to see LLR IDs and trace details.
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { FileCode, AlertTriangle, Save, Pencil, Eye } from 'lucide-react';
import CodeFileEditor, { type LineDecoration } from '@/components/CodeFileEditor';

// ── Types ────────────────────────────────────────────────────────────────────

export interface LineTrace {
  start: number;
  end: number;
  llr_ids: string[];
  symbol: string;
  case_ids?: string[];
  class_name?: string;
}

export interface SuggestedTrace {
  function_name: string;
  start: number;
  end: number;
  suggested_llr_ids: string[];
  rationale: string;
  confidence: 'high' | 'medium' | 'low';
}

export interface UntracedFunction {
  name: string;
  start: number;
  end: number;
  is_private: boolean;
  class_name?: string;
}

interface CodeTraceViewProps {
  filePath: string;
  lineTraces: LineTrace[];
  suggestedTraces?: SuggestedTrace[];
  untracedFunctions?: UntracedFunction[];
  highlightLlr?: string | null;
  onLlrClick?: (llrId: string) => void;
  llrColorMap?: Map<string, string>;
  scrollToLine?: number | null;
  scrollToLineEnd?: number | null;
  selectedRequirementIds?: Set<string>;
}

// ── Decoration builder ───────────────────────────────────────────────────────

function buildTraceDecorations(
  lineTraces: LineTrace[],
  suggestedTraces: SuggestedTrace[],
  untracedFunctions: UntracedFunction[],
  highlightLlr: string | null,
  selectedRequirementIds?: Set<string>,
): LineDecoration[] {
  const decorations: LineDecoration[] = [];
  const hasSelection = selectedRequirementIds && selectedRequirementIds.size > 0;

  for (const trace of lineTraces) {
    const matchesSelection = !hasSelection
      || trace.llr_ids.some(id => selectedRequirementIds!.has(id));
    if (!matchesSelection) continue;

    const isHighlighted = highlightLlr != null
      && trace.llr_ids.includes(highlightLlr);
    const className = isHighlighted
      ? 'trace-line-confirmed-highlight'
      : 'trace-line-confirmed';

    const hoverLines = [
      `**${trace.symbol || 'Traced function'}**`,
      '',
      trace.llr_ids.map(id => `- \`${id}\``).join('\n'),
    ];

    // Build inline label: LLR IDs + CASE IDs as chips
    const chips: string[] = [...trace.llr_ids];
    if (trace.case_ids?.length) chips.push(...trace.case_ids);
    const inlineLabel = chips.join(' ');

    decorations.push({
      startLine: trace.start,
      endLine: trace.end,
      className,
      glyphClassName: 'trace-glyph-confirmed',
      hoverMessage: hoverLines.join('\n'),
      inlineLabel,
    });
  }

  for (const s of suggestedTraces) {
    const hoverLines = [
      `**Suggested: ${s.function_name}** _(${s.confidence})_`,
      '',
      s.suggested_llr_ids.map(id => `- \`${id}\`?`).join('\n'),
      '',
      `_${s.rationale}_`,
    ];

    decorations.push({
      startLine: s.start,
      endLine: s.end,
      className: 'trace-line-suggested',
      glyphClassName: 'trace-glyph-suggested',
      hoverMessage: hoverLines.join('\n'),
    });
  }

  for (const u of untracedFunctions) {
    decorations.push({
      startLine: u.start,
      endLine: u.end,
      className: 'trace-line-untraced',
      glyphClassName: 'trace-glyph-untraced',
      hoverMessage: `**UNTRACED:** \`${u.name}\`\n\nNo LLR trace annotation found.`,
    });
  }

  return decorations;
}

// ── Save helper ──────────────────────────────────────────────────────────────

async function saveFile(filePath: string, content: string): Promise<boolean> {
  const res = await fetch(
    `/api/workspace/file?path=${encodeURIComponent(filePath)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
  );
  return res.ok;
}

// ── Component ────────────────────────────────────────────────────────────────

export default function CodeTraceView({
  filePath, lineTraces, suggestedTraces = [], untracedFunctions = [],
  highlightLlr, onLlrClick: _onLlrClick, llrColorMap: _llrColorMap,
  scrollToLine, scrollToLineEnd,
  selectedRequirementIds,
}: CodeTraceViewProps) {
  const [code, setCode] = useState<string | null>(null);
  const [editedCode, setEditedCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle');

  // Fetch file content
  useEffect(() => {
    const controller = new AbortController();
    setCode(null);
    setEditedCode(null);
    setError(null);
    setEditing(false);
    setSaveStatus('idle');
    fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`, { signal: controller.signal })
      .then(res => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.text();
      })
      .then(text => {
        setCode(text);
        setEditedCode(text);
      })
      .catch(e => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => controller.abort();
  }, [filePath]);

  const allLlrs = useMemo(() => {
    const ids = new Set<string>();
    lineTraces.forEach(t => t.llr_ids.forEach(id => ids.add(id)));
    suggestedTraces.forEach(s => s.suggested_llr_ids.forEach(id => ids.add(id)));
    return Array.from(ids).sort();
  }, [lineTraces, suggestedTraces]);

  const decorations = useMemo(
    () => buildTraceDecorations(
      lineTraces, suggestedTraces, untracedFunctions,
      highlightLlr ?? null, selectedRequirementIds,
    ),
    [lineTraces, suggestedTraces, untracedFunctions, highlightLlr, selectedRequirementIds],
  );

  const handleSave = useCallback(async (content: string) => {
    setSaving(true);
    const ok = await saveFile(filePath, content);
    setSaving(false);
    setSaveStatus(ok ? 'saved' : 'error');
    if (ok) {
      setCode(content);
      setTimeout(() => setSaveStatus('idle'), 2000);
    }
  }, [filePath]);

  const handleToggleEdit = useCallback(() => {
    if (editing) {
      // Discard changes
      setEditedCode(code);
    }
    setEditing(!editing);
    setSaveStatus('idle');
  }, [editing, code]);

  const untracedCount = untracedFunctions.length;
  const suggestedCount = suggestedTraces.length;
  const lines = code?.split('\n').length ?? 0;
  const isDirty = editing && editedCode !== code;

  if (error) {
    return (
      <div className="p-4 text-forge-muted text-xs font-mono">
        Failed to load {filePath}: {error}
      </div>
    );
  }

  if (code === null) {
    return (
      <div className="p-4 text-forge-muted text-xs font-mono animate-pulse">
        Loading {filePath}...
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-forge-border/50 shrink-0 bg-forge-bg/30">
        <FileCode size={14} className="text-forge-muted" />
        <span className="text-xs font-mono text-forge-text">{filePath}</span>

        <span className="text-[10px] text-forge-muted ml-auto flex items-center gap-2">
          {lines} lines
          {allLlrs.length > 0 && <>&middot; {allLlrs.length} LLR(s)</>}
          {untracedCount > 0 && (
            <span className="text-red-400 flex items-center gap-0.5">
              <AlertTriangle size={10} />
              {untracedCount} gap{untracedCount !== 1 ? 's' : ''}
            </span>
          )}
          {suggestedCount > 0 && (
            <span className="text-amber-400">
              {suggestedCount} suggested
            </span>
          )}
        </span>

        {/* Edit/View toggle */}
        <button
          onClick={handleToggleEdit}
          className={`ml-2 flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono transition-colors ${
            editing
              ? 'bg-amber-400/20 text-amber-400 border border-amber-400/30'
              : 'bg-forge-border/30 text-forge-muted hover:text-forge-text'
          }`}
          title={editing ? 'Switch to view mode' : 'Switch to edit mode'}
        >
          {editing ? <Eye size={10} /> : <Pencil size={10} />}
          {editing ? 'View' : 'Edit'}
        </button>

        {/* Save */}
        {editing && (
          <button
            onClick={() => editedCode && handleSave(editedCode)}
            disabled={saving || !isDirty}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono bg-blue-500/20 text-blue-400 border border-blue-500/30 hover:bg-blue-500/30 disabled:opacity-30 transition-colors"
            title="Save (Ctrl+S)"
          >
            <Save size={10} />
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}

        {/* Save status indicator */}
        {saveStatus === 'saved' && (
          <span className="text-[10px] text-green-400 font-mono">Saved</span>
        )}
        {saveStatus === 'error' && (
          <span className="text-[10px] text-red-400 font-mono">Save failed</span>
        )}
      </div>

      {/* Monaco editor */}
      <div className="flex-1 min-h-0">
        <CodeFileEditor
          value={editedCode ?? code}
          filePath={filePath}
          readOnly={!editing}
          onChange={editing ? setEditedCode : undefined}
          onSave={editing ? handleSave : undefined}
          scrollToLine={scrollToLine}
          scrollToLineEnd={scrollToLineEnd}
          decorations={decorations}
        />
      </div>
    </div>
  );
}
