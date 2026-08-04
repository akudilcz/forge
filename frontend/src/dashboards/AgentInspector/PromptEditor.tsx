/**
 * PromptViewer — read-only prompt viewer for a role or gap target.
 * Named PromptEditor for backward compatibility with imports.
 */
import { useQuery } from '@tanstack/react-query';
import { Eye } from 'lucide-react';
import { AGENT_COLOR } from './constants';

export interface RoleTarget  { type: 'role'; key: string }
export interface GapTarget   { type: 'gap';  key: string; role: string }
export type PromptTarget = RoleTarget | GapTarget;

interface PromptResponse {
  prompt: string;
  is_default: boolean;
  inherited_from?: string | null;
}

function apiUrl(target: PromptTarget): string {
  if (target.type === 'role') return `/api/agents/${encodeURIComponent(target.key)}/prompt`;
  return `/api/agents/gaps/${encodeURIComponent(target.key)}/prompt`;
}

async function fetchPrompt(target: PromptTarget): Promise<PromptResponse> {
  const r = await fetch(apiUrl(target));
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

function targetColor(target: PromptTarget): string {
  const role = target.type === 'role' ? target.key : target.role;
  return AGENT_COLOR[role] ?? '#94a3b8';
}

export function PromptEditor({ target }: { target: PromptTarget }) {
  const color = targetColor(target);

  const { data, isLoading } = useQuery<PromptResponse>({
    queryKey: ['agent-prompt', target.type, target.key],
    queryFn: () => fetchPrompt(target),
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-[10px] font-mono text-forge-muted animate-pulse">
        Loading prompt…
      </div>
    );
  }

  const label        = target.type === 'role' ? 'Role Prompt' : 'Gap Prompt';
  const inheritedFrom = data?.inherited_from;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-forge-border shrink-0">
        <span className="text-[9px] font-mono font-semibold uppercase tracking-widest"
          style={{ color }}>
          {label}
        </span>
        <span className="text-[10px] font-mono text-forge-text truncate">{target.key}</span>
        {inheritedFrom && (
          <span className="text-[9px] font-mono text-forge-muted shrink-0">
            ↑ {inheritedFrom}
          </span>
        )}
      </div>

      {/* Read-only prompt text */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        <pre className="text-[10.5px] font-mono leading-relaxed text-forge-text whitespace-pre-wrap break-words">
          {data?.prompt ?? ''}
        </pre>
      </div>
    </div>
  );
}

export function PromptEditorPlaceholder() {
  return (
    <div className="h-full flex flex-col items-center justify-center text-forge-muted/40 gap-2">
      <Eye size={24} className="opacity-30" />
      <p className="text-[10px] font-mono text-center">
        Select a gap chip to view its prompt<br />
        or click an agent role for the role-level prompt
      </p>
    </div>
  );
}
