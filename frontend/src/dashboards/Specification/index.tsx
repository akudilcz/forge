import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { FileText, GitBranch, Save, RefreshCw, CheckCircle2, AlertCircle, Upload } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { ResizableSplit } from '@/components/ResizableSplit';
import { useStore } from '@/store';

const SPEC_TYPES = new Set(['DOCUMENT', 'PARA', 'REQ']);

// ── WhitepaperUploadZone ───────────────────────────────────────────────────

function WhitepaperUploadZone({ onUploaded }: { onUploaded: () => void }) {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append('file', file);
      return fetch('/api/workspace/forgemd', { method: 'POST', body: form }).then(r => {
        if (!r.ok) return r.json().then(e => Promise.reject(new Error(e.detail ?? 'Upload failed')));
        return r.json();
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['forgemd'] });
      qc.invalidateQueries({ queryKey: ['graph-nodes'] });
      onUploaded();
    },
  });

  const startBlank = useMutation({
    mutationFn: () =>
      fetch('/api/workspace/forgemd', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: '# Whitepaper\n\n' }),
      }).then(r => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['forgemd'] });
      qc.invalidateQueries({ queryKey: ['graph-nodes'] });
      onUploaded();
    },
  });

  const logAction = useStore((s) => s.logUserAction);
  const handleFiles = useCallback(
    (files: FileList | null) => { const file = files?.[0]; if (file) { logAction(`Upload file: ${file.name}`); upload.mutate(file); } },
    [upload, logAction],
  );

  const isPending = upload.isPending || startBlank.isPending;

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 gap-6">
      <div
        onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={e => { e.preventDefault(); setIsDragging(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => !isPending && fileInputRef.current?.click()}
        className={`w-full max-w-sm border-2 border-dashed rounded-xl p-10 flex flex-col items-center gap-4 cursor-pointer transition-colors ${
          isDragging
            ? 'border-forge-accent bg-forge-accent/10'
            : 'border-forge-border hover:border-forge-accent/50 hover:bg-forge-bg/40'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.md,.markdown,.rst"
          className="hidden"
          onChange={e => handleFiles(e.target.files)}
        />
        {isPending
          ? <RefreshCw size={32} className="text-forge-accent animate-spin" />
          : <Upload size={32} className="text-forge-muted" />}
        <div className="text-center">
          <p className="text-sm font-mono font-bold text-forge-text">
            {isPending ? 'Uploading…' : 'Upload Forge.md'}
          </p>
          <p className="text-xs font-mono text-forge-muted mt-1">
            Drop a .txt or .md file, or click to browse
          </p>
        </div>
      </div>

      {upload.isError && (
        <p className="text-xs font-mono text-forge-error flex items-center gap-1.5">
          <AlertCircle size={12} /> {(upload.error as Error).message}
        </p>
      )}

      <div className="flex items-center gap-3 w-full max-w-sm">
        <div className="flex-1 h-px bg-forge-border" />
        <span className="text-[10px] font-mono text-forge-muted">or</span>
        <div className="flex-1 h-px bg-forge-border" />
      </div>

      <button
        onClick={() => startBlank.mutate()}
        disabled={isPending}
        className="px-4 py-2 rounded-lg border border-forge-border text-xs font-mono text-forge-muted hover:text-forge-text hover:border-forge-accent/40 transition-colors disabled:opacity-40"
      >
        Start with blank document
      </button>
    </div>
  );
}

// ── WhitepaperEditor ───────────────────────────────────────────────────────

function WhitepaperEditor() {
  const qc = useQueryClient();
  const logAction = useStore((s) => s.logUserAction);
  const [content, setContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { data, isLoading, error, refetch } = useQuery<string>({
    queryKey: ['forgemd'],
    queryFn: () => fetch('/api/workspace/forgemd').then(r => {
      if (!r.ok) throw new Error('Not found');
      return r.text();
    }),
  });

  useEffect(() => {
    if (data !== undefined) { setContent(data); setDirty(false); }
  }, [data]);

  const save = useMutation({
    mutationFn: (text: string) =>
      fetch('/api/workspace/forgemd', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text }),
      }).then(r => r.json()),
    onMutate: () => { setSaveStatus('saving'); logAction('Save Forge.md'); },
    onSuccess: () => {
      setSaveStatus('saved');
      setDirty(false);
      qc.invalidateQueries({ queryKey: ['graph-nodes'] });
      setTimeout(() => setSaveStatus('idle'), 2000);
    },
    onError: () => { setSaveStatus('error'); setTimeout(() => setSaveStatus('idle'), 3000); },
  });

  const showUpload = !isLoading && !!error;

  return (
    <div className="flex flex-col h-full">
      {!showUpload && (
        <div className="flex items-center gap-2 p-2 border-b border-forge-border bg-forge-bg/30 shrink-0">
          <button
            onClick={() => refetch()}
            className="p-1.5 rounded text-forge-muted hover:text-forge-text hover:bg-forge-bg transition-colors"
            title="Reload"
          >
            <RefreshCw size={12} />
          </button>
          <div className="flex-1" />
          {saveStatus === 'saved' && <CheckCircle2 size={12} className="text-forge-success" />}
          {saveStatus === 'error' && <AlertCircle size={12} className="text-forge-error" />}
          {dirty && <span className="text-[10px] font-mono text-forge-muted">unsaved</span>}
          <button
            onClick={() => save.mutate(content)}
            disabled={!dirty || save.isPending}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-mono transition-colors ${
              dirty
                ? 'bg-forge-accent/20 text-forge-accent border border-forge-accent/30 hover:bg-forge-accent/30'
                : 'text-forge-muted border border-forge-border cursor-not-allowed opacity-40'
            }`}
          >
            <Save size={11} />
            {save.isPending ? 'Saving…' : 'Save (Ctrl+S)'}
          </button>
        </div>
      )}

      {isLoading && (
        <div className="flex-1 flex items-center justify-center">
          <RefreshCw size={16} className="text-forge-muted animate-spin" />
        </div>
      )}

      {showUpload && <WhitepaperUploadZone onUploaded={() => refetch()} />}

      {!isLoading && !showUpload && (
        <textarea
          ref={textareaRef}
          value={content}
          onChange={e => { setContent(e.target.value); setDirty(true); setSaveStatus('idle'); }}
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
              e.preventDefault();
              if (dirty) save.mutate(content);
            }
          }}
          className="flex-1 w-full p-4 font-mono text-xs bg-transparent text-forge-text resize-none focus:outline-none leading-relaxed"
          placeholder="# Whitepaper&#10;&#10;Start writing your requirements document…"
          spellCheck={false}
        />
      )}
    </div>
  );
}

// ── Specification ──────────────────────────────────────────────────────────

export function Specification() {
  const { data: allNodes, isLoading, refetch } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const specNodes = (allNodes ?? []).filter(n => SPEC_TYPES.has(n.node_type));

  const counts = specNodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.node_type] = (acc[n.node_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Specification</h1>
          <p className="text-sm text-forge-muted font-mono">
            Whitepaper → paragraphs → formal requirements.
            {specNodes.length > 0 && (
              <span className="ml-2 text-forge-accent">
                {Object.entries(counts).map(([t, c]) => `${c} ${t}`).join(' · ')}
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Two-panel content — NTP left (graph-first), editor right */}
      <div className="flex-1 min-h-0">
        <ResizableSplit
          initialSplit={50}
          minLeft={20}
          maxLeft={80}
          storageKey="specification"
          left={
            <div className="flex flex-col min-h-0 h-full pr-2">
              <NodeTablePanel
                nodes={specNodes}
                isLoading={isLoading}
                title="Spec Nodes"
                icon={<GitBranch size={14} />}
                onRefresh={refetch}
                emptyMessage="No spec nodes yet. Ingest the whitepaper to generate PARA and REQ nodes."
                extraDetail={(node) => <NodeContextPanel node={node} />}
              />
            </div>
          }
          right={
            <div className="flex flex-col min-h-0 h-full pl-2">
              <div className="bg-forge-surface rounded-xl border border-forge-border flex flex-col overflow-hidden h-full">
                <div className="p-3 border-b border-forge-border bg-forge-bg/50 flex items-center gap-2 shrink-0">
                  <FileText size={14} className="text-forge-muted" />
                  <h2 className="text-sm font-bold font-mono text-forge-text uppercase">Forge.md</h2>
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">
                  <WhitepaperEditor />
                </div>
              </div>
            </div>
          }
        />
      </div>
    </div>
  );
}
