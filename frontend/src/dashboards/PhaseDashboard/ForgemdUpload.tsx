/**
 * ForgemdUpload — file upload zone for Phase 1 (Read Forge.md).
 *
 * Allows the user to drag-and-drop or browse for a Markdown file.
 * Regardless of original filename, the backend writes it as the
 * configured forge.md name. After upload, Phase 1 is auto-triggered.
 */
import { useState, useCallback, useRef } from 'react';
import { Upload, FileText, CheckCircle2, AlertCircle } from 'lucide-react';

type UploadState = 'idle' | 'dragging' | 'uploading' | 'success' | 'error';

export function ForgemdUpload({ onUploaded }: { onUploaded?: () => void }) {
  const [state, setState] = useState<UploadState>('idle');
  const [message, setMessage] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(async (file: File) => {
    setState('uploading');
    setMessage(`Uploading ${file.name}...`);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/workspace/forgemd', { method: 'POST', body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail ?? res.statusText);
      }
      const data = await res.json();
      setState('success');
      setMessage(
        `Uploaded (${(data.size / 1024).toFixed(1)} KB)` +
        (data.created ? ` — ${data.created} nodes created` : '') +
        (data.updated ? `, ${data.updated} updated` : ''),
      );
      // Auto-run Phase 1 to ingest the document
      await fetch('/api/phases/1/run', { method: 'POST' });
      onUploaded?.();
    } catch (err) {
      setState('error');
      setMessage(err instanceof Error ? err.message : 'Upload failed');
    }
  }, [onUploaded]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setState('idle');
    const file = e.dataTransfer.files[0];
    if (file) upload(file);
  }, [upload]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setState('dragging');
  }, []);

  const handleDragLeave = useCallback(() => setState('idle'), []);

  const handleBrowse = useCallback(() => inputRef.current?.click(), []);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) upload(file);
    e.target.value = '';
  }, [upload]);

  const borderColor =
    state === 'dragging' ? 'border-forge-accent bg-forge-accent/5' :
    state === 'success'  ? 'border-green-500/40 bg-green-500/5' :
    state === 'error'    ? 'border-red-500/40 bg-red-500/5' :
                           'border-forge-border border-dashed hover:border-forge-accent/40 hover:bg-forge-accent/5';

  return (
    <div className="p-5 border-b border-forge-border/50 shrink-0">
      <p className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-2">
        Upload Document
      </p>
      <div
        className={`rounded-lg border-2 p-6 text-center transition-all cursor-pointer ${borderColor}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={handleBrowse}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".md,.markdown,.txt"
          className="hidden"
          onChange={handleFileChange}
        />
        {state === 'uploading' ? (
          <div className="flex flex-col items-center gap-2">
            <div className="w-5 h-5 border-2 border-forge-accent border-t-transparent rounded-full animate-spin" />
            <p className="text-xs font-mono text-forge-muted">{message}</p>
          </div>
        ) : state === 'success' ? (
          <div className="flex flex-col items-center gap-2">
            <CheckCircle2 size={20} className="text-green-400" />
            <p className="text-xs font-mono text-green-400">{message}</p>
            <p className="text-[10px] font-mono text-forge-muted/50">Drop another file to replace</p>
          </div>
        ) : state === 'error' ? (
          <div className="flex flex-col items-center gap-2">
            <AlertCircle size={20} className="text-red-400" />
            <p className="text-xs font-mono text-red-400">{message}</p>
            <p className="text-[10px] font-mono text-forge-muted/50">Click or drop to try again</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            {state === 'dragging' ? (
              <FileText size={20} className="text-forge-accent" />
            ) : (
              <Upload size={20} className="text-forge-muted/50" />
            )}
            <p className="text-xs font-mono text-forge-muted">
              {state === 'dragging' ? 'Drop to upload' : 'Drop a Markdown file here or click to browse'}
            </p>
            <p className="text-[10px] font-mono text-forge-muted/40">
              File will be saved as the configured forge.md name
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
