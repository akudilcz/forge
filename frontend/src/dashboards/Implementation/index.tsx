import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Code2, FileCode, Folder, File, FolderOpen, RefreshCw, ChevronRight, Layers } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { ResizableSplit } from '@/components/ResizableSplit';
import { ResizableSplitV } from '@/components/ResizableSplitV';

const CODE_TYPES = new Set(['MODULE', 'CLASS', 'FUNC', 'CODE']);

// ── File tree ──────────────────────────────────────────────────────────────

interface TreeNode { name: string; type: 'file' | 'dir' | 'missing'; children?: TreeNode[]; }
interface WorkspaceTree { root: string; tree: TreeNode; }

const EXT_COLOR: Record<string, string> = {
  py: '#3b82f6', ts: '#2563eb', tsx: '#06b6d4', js: '#f59e0b',
  md: '#10b981', json: '#a78bfa', yaml: '#6b7280', sh: '#22d3ee',
};
function fileColor(name: string) {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return EXT_COLOR[ext] ?? '#6b7280';
}

function FileTreeItem({ node, depth, onSelect, selectedPath, pathPrefix }: {
  node: TreeNode; depth: number; onSelect: (p: string) => void;
  selectedPath: string; pathPrefix: string;
}) {
  const [open, setOpen] = useState(depth < 2);
  const fullPath = pathPrefix ? `${pathPrefix}/${node.name}` : node.name;
  const isSelected = selectedPath === fullPath;

  if (node.type === 'file') {
    return (
      <button
        onClick={() => onSelect(fullPath)}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        className={`w-full text-left flex items-center gap-2 py-1 px-2 rounded text-xs font-mono transition-colors hover:bg-forge-bg/50 ${
          isSelected ? 'bg-forge-accent/10 text-forge-accent' : 'text-forge-muted'
        }`}
      >
        <File size={11} style={{ color: fileColor(node.name), flexShrink: 0 }} />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        className="w-full text-left flex items-center gap-2 py-1 px-2 rounded text-xs font-mono text-forge-muted hover:bg-forge-bg/50 transition-colors"
      >
        <ChevronRight size={10} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        {open
          ? <FolderOpen size={11} className="text-forge-warning shrink-0" />
          : <Folder size={11} className="text-forge-warning shrink-0" />}
        <span className="truncate">{node.name}</span>
      </button>
      {open && node.children?.map((child, i) => (
        <FileTreeItem
          key={i} node={child} depth={depth + 1}
          onSelect={onSelect} selectedPath={selectedPath} pathPrefix={fullPath}
        />
      ))}
    </div>
  );
}

function CodeViewer({ path }: { path: string }) {
  const { data, isLoading, error } = useQuery<string>({
    queryKey: ['workspace-file', path],
    queryFn: () => fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`).then(r => {
      if (!r.ok) throw new Error(r.statusText);
      return r.text();
    }),
    enabled: !!path,
  });

  if (!path) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-forge-muted/30">
        <Code2 size={40} className="mb-3 opacity-10" />
        <p className="font-mono text-sm">Select a file to view</p>
      </div>
    );
  }
  if (isLoading) return <div className="flex items-center justify-center h-full"><RefreshCw size={16} className="text-forge-muted animate-spin" /></div>;
  if (error) return <div className="flex flex-col items-center justify-center h-full text-forge-muted gap-2"><Code2 size={24} className="opacity-30" /><p className="text-xs font-mono">Cannot display this file.</p></div>;

  const lines = (data ?? '').split('\n');
  return (
    <div className="h-full overflow-auto">
      <div className="flex min-w-0">
        <div className="select-none text-right pr-4 py-4 pl-4 text-[11px] font-mono text-forge-muted/40 leading-5 bg-forge-bg/40 border-r border-forge-border/30 shrink-0">
          {lines.map((_, i) => <div key={i}>{i + 1}</div>)}
        </div>
        <pre className="flex-1 py-4 pl-4 pr-4 text-[11px] font-mono text-forge-text leading-5 whitespace-pre overflow-x-auto">
          {data}
        </pre>
      </div>
    </div>
  );
}

// ── Implementation ─────────────────────────────────────────────────────────

export function Implementation() {
  const [selectedFile, setSelectedFile] = useState('');

  const { data: treeData, isLoading: treeLoading, refetch: refetchTree } = useQuery<WorkspaceTree>({
    queryKey: ['workspace-tree'],
    queryFn: () => fetch('/api/workspace/tree?depth=5').then(r => r.json()),
    refetchInterval: 30_000,
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

  const codeNodes = (allNodes ?? []).filter(n => CODE_TYPES.has(n.node_type));

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Implementation</h1>
          <p className="text-sm text-forge-muted font-mono">
            Source files and design nodes.
            {codeNodes.length > 0 && (
              <span className="ml-2 text-forge-accent">{codeNodes.length} code nodes</span>
            )}
          </p>
        </div>
      </div>

      {/* Two-panel body — NTP left (graph-first), files right */}
      <div className="flex-1 min-h-0">
        <ResizableSplit
          initialSplit={50}
          minLeft={20}
          maxLeft={80}
          storageKey="implementation"
          left={
            <div className="flex flex-col min-h-0 h-full pr-2">
              <NodeTablePanel
                nodes={codeNodes}
                isLoading={nodesLoading}
                title="Code Nodes"
                icon={<Layers size={14} />}
                onRefresh={refetchNodes}
                emptyMessage="No code nodes yet. Run phases 4–5 to generate MODULE, CLASS, FUNC and CODE nodes."
                extraDetail={(node) => <NodeContextPanel node={node} />}
              />
            </div>
          }
          right={
            <div className="flex flex-col min-h-0 h-full pl-2">
          <ResizableSplitV
            initialSplit={35}
            storageKey="implementation-files"
            top={
              <div className="h-full pb-1.5 bg-forge-surface rounded-xl border border-forge-border flex flex-col overflow-hidden">
                <div className="p-3 border-b border-forge-border flex items-center gap-2 bg-forge-bg/50 shrink-0">
                  <FileCode size={14} className="text-forge-muted" />
                  <h2 className="text-sm font-bold font-mono text-forge-text uppercase">Files</h2>
                  <button onClick={() => refetchTree()} className="ml-auto p-1 rounded text-forge-muted hover:text-forge-text transition-colors">
                    <RefreshCw size={11} />
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto py-1">
                  {treeLoading && <div className="flex items-center justify-center py-8"><RefreshCw size={14} className="text-forge-muted animate-spin" /></div>}
                  {treeData?.tree?.children?.map((child, i) => (
                    <FileTreeItem
                      key={i} node={child} depth={0}
                      onSelect={setSelectedFile} selectedPath={selectedFile} pathPrefix=""
                    />
                  ))}
                  {!treeLoading && !treeData?.tree?.children?.length && (
                    <p className="text-xs font-mono text-forge-muted text-center py-8 px-4">Workspace is empty.</p>
                  )}
                </div>
              </div>
            }
            bottom={
              <div className="h-full pt-1.5 bg-forge-surface rounded-xl border border-forge-border flex flex-col overflow-hidden">
                <div className="p-3 border-b border-forge-border flex items-center gap-2 bg-forge-bg/50 shrink-0">
                  <Code2 size={14} className="text-forge-muted" />
                  <h2 className="text-sm font-bold font-mono text-forge-text uppercase">
                    {selectedFile ? selectedFile.split('/').pop() : 'Editor'}
                  </h2>
                  {selectedFile && (
                    <span className="ml-1 text-[10px] font-mono text-forge-muted/60">{selectedFile}</span>
                  )}
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">
                  <CodeViewer path={selectedFile} />
                </div>
              </div>
            }
          />
            </div>
          }
        />
      </div>
    </div>
  );
}
