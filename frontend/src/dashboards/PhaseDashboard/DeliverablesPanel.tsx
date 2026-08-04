/**
 * DeliverablesPanel — right-panel for Phase 14 (Deliverables) dashboard.
 *
 * Shows the deliverables pack manifest with inline Monaco preview,
 * file counts, and a Download All (.zip) button.
 */

import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  FileText, ChevronRight, ChevronDown, CheckCircle2,
  Download, FolderOpen, Code2, FlaskConical, Settings2,
  Pencil, Eye,
} from 'lucide-react';
import CodeFileEditor from '@/components/CodeFileEditor';

// ── Types ────────────────────────────────────────────────────────────────────

type ManifestFile = { path: string; size: number };
type ManifestResponse = { exists: boolean; files: ManifestFile[] };

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

function FilePreview({ path }: { path: string }) {
  const fullPath = `deliverables/${path}`;
  const { data: content, isLoading, error, refetch } = useQuery<string>({
    queryKey: ['deliverable-doc', path],
    queryFn: async () => {
      const res = await fetch(
        `/api/workspace/file?path=${encodeURIComponent(fullPath)}`,
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
    const ok = await saveFile(fullPath, value);
    setSaving(false);
    setSaveStatus(ok ? 'saved' : 'error');
    if (ok) {
      refetch();
      setTimeout(() => setSaveStatus('idle'), 2000);
    }
  }, [fullPath, refetch]);

  if (isLoading) {
    return (
      <div className="p-4 text-forge-muted text-xs font-mono animate-pulse">
        Loading {path}...
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 text-forge-muted text-xs font-mono">
        Could not load preview.
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
          filePath={path}
          readOnly={!editing}
          onSave={editing ? handleSave : undefined}
        />
      </div>
    </div>
  );
}

// ── File row ─────────────────────────────────────────────────────────────────

function FileRow({ file, isOpen, onToggle }: {
  file: ManifestFile;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const sizeKb = (file.size / 1024).toFixed(1);
  return (
    <div className="border border-forge-border/50 rounded-lg overflow-hidden mb-1.5">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-forge-border/20 transition-colors cursor-pointer"
      >
        {isOpen
          ? <ChevronDown size={12} className="text-forge-muted shrink-0" />
          : <ChevronRight size={12} className="text-forge-muted shrink-0" />
        }
        <FileIcon path={file.path} />
        <span className="text-xs font-mono text-forge-text truncate flex-1 text-left">
          {file.path}
        </span>
        <span className="text-[9px] font-mono text-forge-muted shrink-0">
          {sizeKb} KB
        </span>
        <CheckCircle2 size={10} className="text-green-400/60 shrink-0" />
      </button>
      {isOpen && (
        <div className="border-t border-forge-border/30">
          <FilePreview path={file.path} />
        </div>
      )}
    </div>
  );
}

function FileIcon({ path }: { path: string }) {
  if (path.startsWith('docs/')) return <FileText size={13} className="text-orange-400 shrink-0" />;
  if (path.startsWith('src/')) return <Code2 size={13} className="text-blue-400 shrink-0" />;
  if (path.startsWith('tests/')) return <FlaskConical size={13} className="text-green-400 shrink-0" />;
  if (path === 'README.md') return <FileText size={13} className="text-cyan-400 shrink-0" />;
  return <Settings2 size={13} className="text-forge-muted shrink-0" />;
}

// ── Summary cards ────────────────────────────────────────────────────────────

function Summary({ files }: { files: ManifestFile[] }) {
  const docs = files.filter(f => f.path.startsWith('docs/')).length;
  const src = files.filter(f => f.path.startsWith('src/')).length;
  const tests = files.filter(f => f.path.startsWith('tests/')).length;
  const config = files.filter(f =>
    !f.path.startsWith('docs/') && !f.path.startsWith('src/') &&
    !f.path.startsWith('tests/') && f.path !== 'README.md',
  ).length;

  const cards = [
    { label: 'Docs', value: docs, color: 'text-orange-400' },
    { label: 'Source', value: src, color: 'text-blue-400' },
    { label: 'Tests', value: tests, color: 'text-green-400' },
    { label: 'Config', value: config, color: 'text-forge-muted' },
  ];

  return (
    <div className="grid grid-cols-4 gap-2 mb-4">
      {cards.map(c => (
        <div key={c.label} className="bg-forge-bg/50 rounded-lg border border-forge-border/50 p-2.5 text-center">
          <div className={`text-lg font-bold font-mono ${c.color}`}>{c.value}</div>
          <div className="text-[9px] font-mono text-forge-muted uppercase">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ── Section grouping ─────────────────────────────────────────────────────────

function FileSection({ title, icon, files, openFile, setOpenFile }: {
  title: string;
  icon: React.ReactNode;
  files: ManifestFile[];
  openFile: string | null;
  setOpenFile: (v: string | null) => void;
}) {
  if (files.length === 0) return null;

  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 mb-1.5 px-1">
        {icon}
        <span className="text-[10px] font-mono font-bold text-forge-muted uppercase">
          {title}
        </span>
        <span className="text-[9px] font-mono text-forge-muted/60">
          ({files.length})
        </span>
      </div>
      {files.map(f => (
        <FileRow
          key={f.path}
          file={f}
          isOpen={openFile === f.path}
          onToggle={() => setOpenFile(openFile === f.path ? null : f.path)}
        />
      ))}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export default function DeliverablesPanel() {
  const [openFile, setOpenFile] = useState<string | null>(null);

  const { data: manifest } = useQuery<ManifestResponse>({
    queryKey: ['deliverables-manifest'],
    queryFn: () => fetch('/api/workspace/deliverables/manifest').then(r => r.json()),
    refetchInterval: 10_000,
  });

  const files = manifest?.files ?? [];
  const exists = manifest?.exists ?? false;

  const readme = files.filter(f => f.path === 'README.md');
  const docs = files.filter(f => f.path.startsWith('docs/'));
  const src = files.filter(f => f.path.startsWith('src/'));
  const tests = files.filter(f => f.path.startsWith('tests/'));
  const config = files.filter(f =>
    !f.path.startsWith('docs/') && !f.path.startsWith('src/') &&
    !f.path.startsWith('tests/') && f.path !== 'README.md',
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-4 py-3 border-b border-forge-border/50 shrink-0">
        <h3 className="text-sm font-bold font-mono text-forge-text">
          Deliverables Pack
        </h3>
        <p className="text-[10px] font-mono text-forge-muted mt-0.5">
          Professional documentation bundle with source code and tests
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!exists ? (
          <div className="text-center py-12 text-forge-muted">
            <FolderOpen size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-xs font-mono">No deliverables pack yet</p>
            <p className="text-[10px] font-mono mt-1 opacity-60">
              Run Phase 14 to generate the documentation bundle
            </p>
          </div>
        ) : (
          <>
            <Summary files={files} />

            <FileSection
              title="README"
              icon={<FileText size={11} className="text-cyan-400" />}
              files={readme}
              openFile={openFile}
              setOpenFile={setOpenFile}
            />
            <FileSection
              title="Documentation"
              icon={<FileText size={11} className="text-orange-400" />}
              files={docs}
              openFile={openFile}
              setOpenFile={setOpenFile}
            />
            <FileSection
              title="Source Code"
              icon={<Code2 size={11} className="text-blue-400" />}
              files={src}
              openFile={openFile}
              setOpenFile={setOpenFile}
            />
            <FileSection
              title="Tests"
              icon={<FlaskConical size={11} className="text-green-400" />}
              files={tests}
              openFile={openFile}
              setOpenFile={setOpenFile}
            />
            {config.length > 0 && (
              <FileSection
                title="Configuration"
                icon={<Settings2 size={11} className="text-forge-muted" />}
                files={config}
                openFile={openFile}
                setOpenFile={setOpenFile}
              />
            )}

            <div className="mt-4 pt-3 border-t border-forge-border/30">
              <a
                href="/api/workspace/deliverables/download"
                download="deliverables.zip"
                className="flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-lg bg-forge-accent/20 border border-forge-accent/30 text-forge-accent text-xs font-mono font-bold hover:bg-forge-accent/30 transition-colors"
              >
                <Download size={14} />
                Download All (.zip)
              </a>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
