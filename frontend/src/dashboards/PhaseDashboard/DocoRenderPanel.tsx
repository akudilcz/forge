/**
 * DocoRenderPanel — right-panel for Phase 11 (Doco Render) dashboard.
 *
 * Lists the 8 rendered Markdown files from workspace/docs/ and lets the
 * user click to preview each one inline with syntax highlighting and editing.
 */

import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  FileText, ChevronRight, ChevronDown, CheckCircle2,
  AlertCircle, Pencil, Eye,
} from 'lucide-react';
import CodeFileEditor from '@/components/CodeFileEditor';

// ── Doc catalogue ────────────────────────────────────────────────────────────

const DOCS = [
  { file: '03-HLR.md',          phase: 3,  title: 'High-Level Requirements' },
  { file: '04-Architecture.md', phase: 4,  title: 'Architecture' },
  { file: '05-Modules.md',      phase: 5,  title: 'Modules' },
  { file: '06-Contracts.md',    phase: 6,  title: 'Contracts' },
  { file: '07-LLR.md',          phase: 7,  title: 'Low-Level Requirements' },
  { file: '08-Design.md',       phase: 8,  title: 'Design Specifications' },
  { file: '09-Test-Suite.md',   phase: 9,  title: 'Test Suite Strategy' },
  { file: '10-Verification.md', phase: 10, title: 'Verification Cases' },
] as const;

// ── Save helper ──────────────────────────────────────────────────────────────

async function saveFile(path: string, content: string): Promise<boolean> {
  const res = await fetch(
    `/api/workspace/file?path=${encodeURIComponent(path)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
  );
  return res.ok;
}

// ── Doc preview (Monaco) ─────────────────────────────────────────────────────

function DocPreview({ file }: { file: string }) {
  const filePath = `docs/${file}`;
  const { data: content, isLoading, error, refetch } = useQuery<string>({
    queryKey: ['workspace-doc', file],
    queryFn: async () => {
      const res = await fetch(
        `/api/workspace/file?path=${encodeURIComponent(filePath)}`,
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.text();
    },
    staleTime: 30_000,
  });

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle');

  const handleSave = useCallback(async (value: string) => {
    setSaving(true);
    const ok = await saveFile(filePath, value);
    setSaving(false);
    setSaveStatus(ok ? 'saved' : 'error');
    if (ok) {
      refetch();
      setTimeout(() => setSaveStatus('idle'), 2000);
    }
  }, [filePath, refetch]);

  if (isLoading) {
    return (
      <div className="p-4 text-forge-muted text-xs font-mono animate-pulse">
        Loading docs/{file}...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 flex items-center gap-2 text-forge-muted text-xs font-mono">
        <AlertCircle size={12} className="text-forge-warning shrink-0" />
        Not yet rendered — run Phase 11 first.
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-forge-border/30 bg-forge-bg/20">
        <button
          onClick={() => setEditing(!editing)}
          className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono transition-colors ${
            editing
              ? 'bg-amber-400/20 text-amber-400 border border-amber-400/30'
              : 'bg-forge-border/30 text-forge-muted hover:text-forge-text'
          }`}
        >
          {editing ? <Eye size={10} /> : <Pencil size={10} />}
          {editing ? 'View' : 'Edit'}
        </button>
        {editing && (
          <span className="text-[9px] font-mono text-forge-muted">Ctrl+S to save</span>
        )}
        {saving && <span className="text-[10px] text-forge-muted font-mono">Saving...</span>}
        {saveStatus === 'saved' && <span className="text-[10px] text-green-400 font-mono">Saved</span>}
        {saveStatus === 'error' && <span className="text-[10px] text-red-400 font-mono">Save failed</span>}
      </div>

      {/* Editor */}
      <div style={{ height: 400 }}>
        <CodeFileEditor
          value={content ?? ''}
          filePath={file}
          readOnly={!editing}
          onSave={editing ? handleSave : undefined}
        />
      </div>
    </div>
  );
}

// ── Doc row ──────────────────────────────────────────────────────────────────

function DocRow({ doc, isOpen, onToggle }: {
  doc: typeof DOCS[number];
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border border-forge-border/50 rounded-lg overflow-hidden mb-2">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-forge-border/20 transition-colors"
      >
        {isOpen
          ? <ChevronDown size={12} className="text-forge-muted shrink-0" />
          : <ChevronRight size={12} className="text-forge-muted shrink-0" />
        }
        <FileText size={13} className="text-orange-400 shrink-0" />
        <span className="text-xs font-mono text-forge-text truncate flex-1 text-left">
          docs/{doc.file}
        </span>
        <span className="text-[9px] font-mono text-forge-muted shrink-0">
          Phase {doc.phase}
        </span>
      </button>
      {isOpen && (
        <div className="border-t border-forge-border/30">
          <DocPreview file={doc.file} />
        </div>
      )}
    </div>
  );
}

// ── Summary ──────────────────────────────────────────────────────────────────

function DocSummary({ rendered }: { rendered: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 mb-4">
      <div className="bg-forge-bg/50 rounded-lg border border-forge-border/50 p-3 text-center">
        <div className="text-lg font-bold font-mono text-forge-text">{DOCS.length}</div>
        <div className="text-[9px] font-mono text-forge-muted uppercase">Total Docs</div>
      </div>
      <div className="bg-forge-bg/50 rounded-lg border border-forge-border/50 p-3 text-center">
        <div className="text-lg font-bold font-mono text-orange-400">{rendered}</div>
        <div className="text-[9px] font-mono text-forge-muted uppercase">Rendered</div>
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export default function DocoRenderPanel() {
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  const { data: tree } = useQuery<{ tree: { children?: Array<{ name: string; type: string; children?: Array<{ name: string }> }> } }>({
    queryKey: ['workspace-tree'],
    queryFn: () => fetch('/api/workspace/tree?depth=2').then(r => r.json()),
    refetchInterval: 15_000,
  });

  const docsDir = tree?.tree?.children?.find(c => c.name === 'docs');
  const existingFiles = new Set(
    docsDir?.children?.map(c => c.name) ?? [],
  );
  const renderedCount = DOCS.filter(d => existingFiles.has(d.file)).length;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-forge-border/50 shrink-0">
        <h3 className="text-sm font-bold font-mono text-forge-text">
          Rendered Documentation
        </h3>
        <p className="text-[10px] font-mono text-forge-muted mt-0.5">
          Deterministic Markdown render of graph phases 3–10
        </p>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <DocSummary rendered={renderedCount} />
        {DOCS.map(doc => {
          const exists = existingFiles.has(doc.file);
          return (
            <div key={doc.file} className="relative">
              {exists && (
                <CheckCircle2
                  size={10}
                  className="absolute right-2 top-2.5 text-green-400/60 z-10"
                />
              )}
              <DocRow
                doc={doc}
                isOpen={openDoc === doc.file}
                onToggle={() => setOpenDoc(openDoc === doc.file ? null : doc.file)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
