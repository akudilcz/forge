/**
 * NodeTablePanel — reusable split-panel for browsing graph nodes.
 *
 * Left:  searchable list + type chip filter.
 * Right: detail panel — Content / Properties / Edges stacked (no tabs).
 */
import { useState } from 'react';

import { Search, RefreshCw, X } from 'lucide-react';
import { TYPE_COLOR, TYPE_SORT_ORDER, resolveTypeKey, nodeTypeClass } from '@/lib/nodeColors';
import { useStore } from '@/store';

export interface GNode {
  node_id: string;
  node_type: string;
  layer: number;
  title: string;
  lifecycle: string;
  version: number;
  content: string;
  content_hash: string;
  parent_id: string | null;
  trace_to: string[];
  properties: Record<string, unknown>;
}

// Re-export so existing importers keep working.
export { TYPE_COLOR };


// ── TypeChips ──────────────────────────────────────────────────────────────────

function TypeChips({
  types, typeCounts, activeType, onSelect,
}: {
  types: string[];
  typeCounts: Record<string, number>;
  activeType: string;
  onSelect: (t: string) => void;
}) {
  if (types.length <= 1) return null;
  const total = Object.values(typeCounts).reduce((a, b) => a + b, 0);

  return (
    <div className="flex flex-wrap gap-1 px-2 pt-1.5 pb-1.5 border-b border-forge-border shrink-0">
      <button
        onClick={() => onSelect('')}
        className={`px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase border transition-colors ${
          activeType === ''
            ? 'bg-forge-accent/20 border-forge-accent/40 text-forge-accent'
            : 'border-forge-border/50 text-forge-muted hover:text-forge-text'
        }`}
      >
        ALL <span className="opacity-60 ml-0.5">{total}</span>
      </button>
      {types.map(t => {
        const style = TYPE_COLOR[t] ?? 'text-slate-400 bg-slate-400/10 border-slate-400/20';
        const isActive = activeType === t;
        return (
          <button
            key={t}
            onClick={() => onSelect(isActive ? '' : t)}
            className={`px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase border transition-colors ${
              isActive ? style : 'border-forge-border/50 text-forge-muted hover:text-forge-text hover:border-forge-border'
            }`}
          >
            {t} <span className="opacity-60 ml-0.5">{typeCounts[t]}</span>
          </button>
        );
      })}
    </div>
  );
}

// ── NodeDetail ─────────────────────────────────────────────────────────────────

function NodeDetail({
  node, onClose, extraDetail,
}: {
  node: GNode;
  onClose: () => void;
  extraDetail?: (node: GNode) => React.ReactNode;
}) {
  const propEntries = Object.entries(node.properties);
  const typeStyle = nodeTypeClass(node);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-forge-border bg-forge-bg/30 shrink-0">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border ${typeStyle}`}>
                {node.node_type}
                {node.node_type === 'REQ' && typeof node.properties.req_level === 'string' && (
                  <span className="opacity-70 ml-1 normal-case">
                    {node.properties.req_level.toUpperCase()}
                  </span>
                )}
              </span>
              <div className="flex items-center gap-1">
                <span className="text-[10px] font-mono text-forge-muted">{node.lifecycle}</span>
              </div>
              <span className="text-[10px] font-mono text-forge-muted/60">v{node.version}</span>
              <span className="text-[10px] font-mono text-forge-muted/40">L{node.layer}</span>
            </div>
            <p className="text-sm font-mono text-forge-text font-semibold leading-snug mb-1">
              {node.title || '(no label)'}
            </p>
            <p className="text-[10px] font-mono text-forge-muted/60 break-all">{node.node_id}</p>
            {node.parent_id && (
              <p className="text-[10px] font-mono text-forge-muted/40 mt-0.5">↳ {node.parent_id}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-forge-bg text-forge-muted hover:text-forge-text transition-colors shrink-0"
          >
            <X size={12} />
          </button>
        </div>
      </div>

      {/* Stacked sections — no tabs */}
      <div className="flex-1 overflow-y-auto text-xs font-mono divide-y divide-forge-border/40">

        {/* Content */}
        <section className="p-4">
          <h4 className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-2">
            Content
          </h4>
          {node.content ? (
            <pre className="text-forge-text whitespace-pre-wrap leading-relaxed break-words">
              {node.content}
            </pre>
          ) : (
            <p className="text-forge-muted/40 italic">No content.</p>
          )}
        </section>

        {/* Properties — hidden when empty */}
        {propEntries.length > 0 && (
          <section className="p-4">
            <h4 className="text-[9px] font-mono text-forge-muted uppercase tracking-wider mb-2">
              Properties <span className="opacity-60">({propEntries.length})</span>
            </h4>
            <table className="w-full">
              <tbody>
                {propEntries.map(([k, v]) => (
                  <tr key={k} className="border-b border-forge-border/20 last:border-0">
                    <td className="py-1.5 pr-4 text-forge-muted align-top w-2/5 break-all">{k}</td>
                    <td className="py-1.5 text-forge-text break-all">
                      {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* Extra detail injected by parent dashboard */}
        {extraDetail && extraDetail(node)}
      </div>
    </div>
  );
}

// ── NodeTablePanel ─────────────────────────────────────────────────────────────

export function NodeTablePanel({
  nodes,
  isLoading,
  title,
  icon,
  onRefresh,
  emptyMessage = 'No nodes yet.',
  extraDetail,
  externalSelectedId,
  onNodeClick,
  selectedIds,
  onNodeToggle,
  onResetSelection,
}: {
  nodes: GNode[];
  isLoading: boolean;
  title: string;
  icon?: React.ReactNode;
  onRefresh?: () => void;
  emptyMessage?: string;
  /** Render extra sections below properties in the detail panel. */
  extraDetail?: (node: GNode) => React.ReactNode;
  /** When set, overrides internal selection — the given node_id is highlighted. */
  externalSelectedId?: string | null;
  /** When set, called instead of updating internal selection state. */
  onNodeClick?: (node: GNode) => void;
  /** Multi-select: set of selected node IDs. */
  selectedIds?: Set<string>;
  /** Multi-select: toggle handler (receives node + shiftKey). */
  onNodeToggle?: (node: GNode, shiftKey: boolean) => void;
  /** Multi-select: reset all selections. */
  onResetSelection?: () => void;
}) {
  const logAction = useStore((s) => s.logUserAction);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [internalSelected, setInternalSelected] = useState<GNode | null>(null);
  const [detailNodeId, setDetailNodeId] = useState<string | null>(null);

  // Multi-select mode uses selectedIds; legacy mode uses externalSelectedId
  const isMultiSelect = selectedIds !== undefined;

  // Controlled vs uncontrolled: if externalSelectedId is provided, derive from nodes.
  const selected = isMultiSelect
    ? (detailNodeId ? (nodes.find(n => n.node_id === detailNodeId) ?? null) : null)
    : externalSelectedId !== undefined
      ? (nodes.find(n => n.node_id === externalSelectedId) ?? null)
      : internalSelected;

  const types = [...new Set(nodes.map(n => n.node_type))].sort(
    (a, b) => (TYPE_SORT_ORDER[a] ?? 99) - (TYPE_SORT_ORDER[b] ?? 99),
  );
  const typeCounts = nodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.node_type] = (acc[n.node_type] ?? 0) + 1;
    return acc;
  }, {});
  const useDropdown = types.length > 8;

  const filtered = nodes.filter(n => {
    if (typeFilter && n.node_type !== typeFilter) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      n.node_id.toLowerCase().includes(q) ||
      n.title.toLowerCase().includes(q) ||
      n.content.toLowerCase().includes(q)
    );
  }).sort((a, b) =>
    (TYPE_SORT_ORDER[a.node_type] ?? 99) - (TYPE_SORT_ORDER[b.node_type] ?? 99)
    || a.node_id.localeCompare(b.node_id),
  );

  return (
    <div className="flex-1 flex min-h-0 rounded-xl border border-forge-border overflow-hidden">
      {/* ── Left list ── */}
      <div
        className={`flex flex-col bg-forge-surface border-r border-forge-border transition-all ${
          selected ? 'w-[38%] shrink-0' : 'w-full'
        }`}
      >
        {/* Header bar */}
        <div className="p-3 border-b border-forge-border bg-forge-bg/50 flex items-center gap-2 shrink-0">
          {icon && <span className="text-forge-muted shrink-0">{icon}</span>}
          <h2 className="text-sm font-bold font-mono text-forge-text uppercase">{title}</h2>
          <span className="text-[10px] font-mono text-forge-muted ml-auto">
            {filtered.length}{nodes.length !== filtered.length ? `/${nodes.length}` : ''}
          </span>
          {onRefresh && (
            <button
              onClick={onRefresh}
              className="p-1 rounded text-forge-muted hover:text-forge-text transition-colors"
            >
              <RefreshCw size={12} />
            </button>
          )}
        </div>

        {/* Search */}
        <div className="px-2 pt-2 pb-1 border-b border-forge-border shrink-0">
          <div className="relative">
            <Search size={10} className="absolute left-2 top-1/2 -translate-y-1/2 text-forge-muted pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search id, label, content…"
              className="w-full pl-6 pr-2 py-1.5 bg-forge-bg border border-forge-border rounded text-xs font-mono text-forge-text placeholder-forge-muted/40 focus:outline-none focus:border-forge-accent/50"
            />
          </div>
        </div>

        {/* Type filter — chips or dropdown */}
        {useDropdown ? (
          <div className="px-2 py-1.5 border-b border-forge-border shrink-0">
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              className="w-full px-2 py-1.5 bg-forge-bg border border-forge-border rounded text-xs font-mono text-forge-text focus:outline-none focus:border-forge-accent/50"
            >
              <option value="">All types</option>
              {types.map(t => <option key={t} value={t}>{t} ({typeCounts[t]})</option>)}
            </select>
          </div>
        ) : (
          <TypeChips
            types={types}
            typeCounts={typeCounts}
            activeType={typeFilter}
            onSelect={setTypeFilter}
          />
        )}

        {/* Reset selection button — multi-select mode only */}
        {isMultiSelect && selectedIds.size > 0 && (
          <div className="px-2 py-1 border-b border-forge-border shrink-0">
            <button
              onClick={onResetSelection}
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase border border-amber-400/30 text-amber-400 bg-amber-400/10 hover:bg-amber-400/20 transition-colors"
            >
              <X size={9} /> Reset ({selectedIds.size})
            </button>
          </div>
        )}

        {/* Rows */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw size={16} className="text-forge-muted animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-xs font-mono text-forge-muted/60 text-center py-12 px-4">
              {search || typeFilter ? 'No matches.' : emptyMessage}
            </p>
          ) : (
            filtered.map(n => {
              const isSelected = isMultiSelect
                ? selectedIds.has(n.node_id)
                : selected?.node_id === n.node_id;
              const typeStyle = TYPE_COLOR[resolveTypeKey(n)] ?? 'text-slate-400 bg-slate-400/10 border-slate-400/20';
              const preview   = n.content?.replace(/\s+/g, ' ').trim().slice(0, 90);
              const reqLevel  = n.node_type === 'REQ' ? (n.properties.req_level as string | undefined) : undefined;
              return (
                <button
                  key={n.node_id}
                  onClick={(e) => {
                    logAction(`Select node: ${n.node_id} (${n.node_type})`);
                    if (onNodeToggle) {
                      onNodeToggle(n, e.shiftKey);
                      // Toggle detail panel: last clicked opens detail, same click closes
                      setDetailNodeId(prev => prev === n.node_id ? null : n.node_id);
                    } else if (onNodeClick) {
                      onNodeClick(n);
                    } else {
                      setInternalSelected(isSelected ? null : n);
                    }
                  }}
                  className={`w-full text-left px-3 py-2.5 border-b border-forge-border/30 transition-colors flex items-start gap-2.5 ${
                    isSelected ? 'bg-forge-accent/10' : 'hover:bg-forge-bg/60'
                  }`}
                >
                  <span className={`text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border shrink-0 mt-0.5 ${typeStyle}`}>
                    {n.node_type}{reqLevel && <span className="opacity-70 ml-0.5">{reqLevel.toUpperCase()}</span>}
                  </span>
                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border shrink-0 mt-0.5
                    text-forge-muted/60 bg-forge-muted/5 border-forge-muted/20">
                    {n.node_id}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-mono text-forge-text font-medium truncate">
                      {n.title || n.node_id}
                    </p>
                    {preview && (
                      <p className="text-[10px] font-mono text-forge-muted/70 truncate mt-0.5">
                        {preview}{(n.content?.length ?? 0) > 90 ? '…' : ''}
                      </p>
                    )}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* ── Right detail ── */}
      {selected && (
        <div className="flex-1 min-w-0 bg-forge-surface flex flex-col overflow-hidden">
          <NodeDetail
            node={selected}
            onClose={() => {
              if (isMultiSelect) setDetailNodeId(null);
              else if (onNodeClick) onNodeClick(selected);
              else setInternalSelected(null);
            }}
            extraDetail={extraDetail}
          />
        </div>
      )}
    </div>
  );
}
