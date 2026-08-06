/**
 * Agent Inspector — agent cards (left) + phase-grouped gap chips + prompt editor (right).
 *
 * Left panel:  agent cards — model, goal, tools, gap responsibilities, live stats.
 * Right panel: ResizableSplitV —
 *   Top:    PhaseGapSelector (phases 2-13 grouped rows) or ReactAgentCanvas (lifecycle toggle)
 *   Bottom: PromptContextStrip + PromptEditor for the selected target
 *
 * Selection model: only ONE thing at a time — gap chip OR agent role.
 * Gap chip → gap prompt  |  Agent button/card → role prompt  |  Clear → placeholder
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Bot, Scissors, List, Layers, Code2, FlaskConical, ShieldCheck, RefreshCw,
  type LucideIcon,
} from 'lucide-react';
import { ResizableSplit } from '@/components/ResizableSplit';
import { ResizableSplitV } from '@/components/ResizableSplitV';
import { useStore } from '@/store';
import { GAP_AGENT_ROLE, GAP_PHASE, PHASE_CONFIG, QUALITY_GAP_TYPES, type PhaseConfig } from '@/lib/phaseConfig';
import { PHASE_COLOR } from '@/lib/nodeColors';
import { ReactAgentCanvas } from './ReactAgentCanvas';
import { PromptEditor, PromptEditorPlaceholder, type PromptTarget } from './PromptEditor';
import { AGENT_COLOR, type AgentDefinition, type ApiAgentState } from './constants';

// ── Agent icon lookup ──────────────────────────────────────────────────────────

const AGENT_ICON: Record<string, LucideIcon> = {
  'Document Specialist':   Scissors,
  'Requirements Engineer': List,
  'Design Architect':      Layers,
  'Software Engineer':     Code2,
  'Test Engineer':         FlaskConical,
  'Quality Auditor':       ShieldCheck,
};

// ── Status badge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'WORKING' || status === 'RUNNING'  ? 'text-green-400  border-green-400/30  bg-green-400/10  animate-pulse' :
    status === 'THINKING' || status === 'WAITING' ? 'text-amber-400  border-amber-400/30  bg-amber-400/10' :
    status === 'BLOCKED'  || status === 'ERROR'   ? 'text-red-400    border-red-400/30    bg-red-400/10'   :
    'text-forge-muted border-forge-border bg-forge-bg';
  return (
    <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border uppercase tracking-wider ${cls}`}>
      {status || 'IDLE'}
    </span>
  );
}

// ── Agent card ─────────────────────────────────────────────────────────────────

function AgentCard({
  def, runtime, selected, onClick,
}: {
  def: AgentDefinition;
  runtime: ApiAgentState | undefined;
  selected: boolean;
  onClick: () => void;
}) {
  const color = AGENT_COLOR[def.role] ?? '#94a3b8';
  const Icon = AGENT_ICON[def.role] ?? Bot;
  const status = runtime?.status ?? 'IDLE';

  return (
    <button
      onClick={onClick}
      className="w-full text-left p-4 rounded-xl border transition-all"
      style={{
        background: selected ? color + '12' : undefined,
        borderColor: selected ? color : 'rgb(var(--forge-border))',
      }}
    >
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: color + '20', border: `1px solid ${color}40` }}>
            <Icon size={15} style={{ color }} />
          </div>
          <div>
            <p className="font-bold text-sm text-forge-text leading-tight">{def.role}</p>
            <p className="text-[10px] font-mono" style={{ color: color + 'aa' }}>{def.model}</p>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>

      <p className="text-xs text-forge-muted italic mb-3 leading-relaxed">{def.goal}</p>

      {runtime?.current_task && (
        <p className="text-[10px] font-mono text-forge-accent bg-forge-accent/10 p-2 rounded
          border border-forge-accent/20 mb-3 line-clamp-2 leading-relaxed">
          {runtime.current_task}
        </p>
      )}

      {runtime && (runtime.tasks_completed > 0 || runtime.tokens_used > 0) && (
        <div className="flex gap-4 mb-3 text-[10px] font-mono text-forge-muted">
          <span>tasks: <span className="text-forge-text">{runtime.tasks_completed}</span></span>
          <span>tokens: <span className="text-forge-text">{runtime.tokens_used.toLocaleString()}</span></span>
        </div>
      )}

      <div className="flex flex-wrap gap-1 mb-2">
        {def.tools.map(t => (
          <span key={t} className="text-[9px] font-mono px-1.5 py-0.5 rounded
            bg-forge-bg border border-forge-border text-forge-muted">
            {t}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap gap-1">
        {def.gap_types.map(g => (
          <span key={g} className="text-[9px] font-mono px-1.5 py-0.5 rounded border"
            style={{ color, background: color + '15', borderColor: color + '40' }}>
            {g}
          </span>
        ))}
      </div>
    </button>
  );
}

// ── PhaseGapSelector ──────────────────────────────────────────────────────────

function PhaseGapSelector({
  selectedGap, selectedRole, onGapClick, onRoleClick,
}: {
  selectedGap: string | null;
  selectedRole: string | null;
  onGapClick: (gap: string) => void;
  onRoleClick: (role: string) => void;
}) {
  const phaseEntries = (Object.entries(PHASE_CONFIG) as [string, PhaseConfig][])
    .map(([n, cfg]) => [Number(n), cfg] as const)
    .filter(([, cfg]) => cfg.gapTypes.length > 0);

  return (
    <div className="h-full overflow-y-auto p-3 space-y-1.5">
      {phaseEntries.map(([phaseNum, cfg]) => {
        const color = PHASE_COLOR[phaseNum] ?? '#94a3b8';
        const role = cfg.gapTypes[0] ? (GAP_AGENT_ROLE[cfg.gapTypes[0]] ?? '') : '';
        const agentColor = role ? (AGENT_COLOR[role] ?? '#94a3b8') : '#94a3b8';
        const isRoleSelected = selectedRole === role && role !== '';
        return (
          <PhaseRow
            key={phaseNum}
            phaseNum={phaseNum}
            phaseName={cfg.name}
            color={color}
            role={role}
            agentColor={agentColor}
            isRoleSelected={isRoleSelected}
            gapTypes={cfg.gapTypes}
            selectedGap={selectedGap}
            onGapClick={onGapClick}
            onRoleClick={onRoleClick}
          />
        );
      })}

      {/* Quality section */}
      <QualityRow
        selectedGap={selectedGap}
        selectedRole={selectedRole}
        onGapClick={onGapClick}
        onRoleClick={onRoleClick}
      />
    </div>
  );
}

function PhaseRow({
  phaseNum, phaseName, color, role, agentColor, isRoleSelected,
  gapTypes, selectedGap, onGapClick, onRoleClick,
}: {
  phaseNum: number; phaseName: string; color: string;
  role: string; agentColor: string; isRoleSelected: boolean;
  gapTypes: string[]; selectedGap: string | null;
  onGapClick: (g: string) => void; onRoleClick: (r: string) => void;
}) {
  return (
    <div className="flex gap-2 rounded-lg border border-forge-border/40 bg-forge-surface/30 px-3 py-2"
      style={{ borderLeftColor: color, borderLeftWidth: 3 }}>
      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border shrink-0 self-start mt-0.5"
        style={{ color, background: color + '15', borderColor: color + '40' }}>
        {phaseNum}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
          <span className="text-[10px] font-mono text-forge-text">{phaseName}</span>
          {role && (
            <button onClick={() => onRoleClick(role)}
              className="text-[9px] font-mono px-2 py-0.5 rounded border transition-colors"
              style={{
                color: isRoleSelected ? agentColor : agentColor + '80',
                background: isRoleSelected ? agentColor + '20' : 'transparent',
                borderColor: isRoleSelected ? agentColor + '60' : agentColor + '25',
              }}>
              {role}
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {gapTypes.map(gap => {
            const isSelected = selectedGap === gap;
            return (
              <button key={gap} onClick={() => onGapClick(gap)}
                className="text-[9px] font-mono px-2 py-0.5 rounded border transition-colors"
                style={{
                  color: isSelected ? color : color + '80',
                  background: isSelected ? color + '20' : 'transparent',
                  borderColor: isSelected ? color + '60' : color + '25',
                }}>
                {gap}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function QualityRow({
  selectedGap, selectedRole, onGapClick, onRoleClick,
}: {
  selectedGap: string | null; selectedRole: string | null;
  onGapClick: (g: string) => void; onRoleClick: (r: string) => void;
}) {
  const c = '#94a3b8';
  const isRoleSelected = selectedRole === 'Quality Auditor';
  return (
    <div className="flex gap-2 rounded-lg border border-forge-border/40 bg-forge-surface/30 px-3 py-2"
      style={{ borderLeftColor: c, borderLeftWidth: 3 }}>
      <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border shrink-0 self-start mt-0.5"
        style={{ color: c, background: c + '15', borderColor: c + '40' }}>
        Q
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[10px] font-mono text-forge-text">Quality</span>
          <button onClick={() => onRoleClick('Quality Auditor')}
            className="text-[9px] font-mono px-2 py-0.5 rounded border transition-colors"
            style={{
              color: isRoleSelected ? c : c + '80',
              background: isRoleSelected ? c + '20' : 'transparent',
              borderColor: isRoleSelected ? c + '60' : c + '25',
            }}>
            Quality Auditor
          </button>
        </div>
        <div className="flex flex-wrap gap-1">
          {QUALITY_GAP_TYPES.map(gap => {
            const isSelected = selectedGap === gap;
            return (
              <button key={gap} onClick={() => onGapClick(gap)}
                className="text-[9px] font-mono px-2 py-0.5 rounded border transition-colors"
                style={{
                  color: isSelected ? c : c + '80',
                  background: isSelected ? c + '20' : 'transparent',
                  borderColor: isSelected ? c + '60' : c + '25',
                }}>
                {gap}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── PromptContextStrip ────────────────────────────────────────────────────────

function PromptContextStrip({ target }: { target: PromptTarget }) {
  if (target.type === 'gap') {
    const phaseNum = GAP_PHASE[target.key];
    const phaseColor = phaseNum !== undefined ? (PHASE_COLOR[phaseNum] ?? '#94a3b8') : '#94a3b8';
    const phaseName = phaseNum !== undefined ? (PHASE_CONFIG[phaseNum]?.name ?? '') : '';
    const agentColor = AGENT_COLOR[target.role] ?? '#94a3b8';
    return (
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-forge-border shrink-0
        bg-forge-bg/50 flex-wrap">
        {phaseNum !== undefined && (
          <span className="text-[9px] font-mono px-1.5 py-0.5 rounded border"
            style={{ color: phaseColor, background: phaseColor + '15', borderColor: phaseColor + '40' }}>
            Phase {phaseNum} · {phaseName}
          </span>
        )}
        <span className="text-[9px] font-mono font-semibold" style={{ color: phaseColor }}>
          {target.key}
        </span>
        <span className="text-[9px] font-mono text-forge-border">·</span>
        <span className="text-[9px] font-mono" style={{ color: agentColor }}>{target.role}</span>
      </div>
    );
  }

  const agentColor = AGENT_COLOR[target.key] ?? '#94a3b8';
  return (
    <div className="flex items-center gap-2 px-4 py-1.5 border-b border-forge-border shrink-0
      bg-forge-bg/50">
      <span className="text-[9px] font-mono text-forge-muted">Agent Role</span>
      <span className="text-[9px] font-mono font-semibold" style={{ color: agentColor }}>
        {target.key}
      </span>
    </div>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────

function RightPanel({
  definitions, runtimeMap,
  selectedRole, selectedGap,
  onRoleClick, onGapClick, onClear,
}: {
  definitions: AgentDefinition[];
  runtimeMap: Map<string, ApiAgentState>;
  selectedRole: string | null;
  selectedGap: string | null;
  onRoleClick: (role: string) => void;
  onGapClick: (gap: string) => void;
  onClear: () => void;
}) {
  const [showLifecycle, setShowLifecycle] = useState(false);
  const selectedDef = definitions.find(d => d.role === selectedRole);

  const promptTarget: PromptTarget | null = selectedGap
    ? { type: 'gap', key: selectedGap, role: GAP_AGENT_ROLE[selectedGap] ?? '' }
    : selectedRole
    ? { type: 'role', key: selectedRole }
    : null;

  const canvas = (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--graph-canvas)' }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-forge-border shrink-0" style={{ background: 'var(--graph-canvas)' }}>
        <span className="text-[10px] font-mono text-forge-muted">Gap Prompts</span>
        {selectedDef && (
          <button onClick={() => setShowLifecycle(v => !v)}
            className={`px-3 py-1 rounded text-[10px] font-mono border transition-colors
              ${showLifecycle
                ? 'bg-forge-accent/20 border-forge-accent/40 text-forge-accent'
                : 'bg-forge-surface border-forge-border text-forge-muted hover:text-forge-text'}`}>
            ◈ Lifecycle · {selectedDef.role.split(' ')[0]}
          </button>
        )}
        {(selectedRole || selectedGap) && (
          <button onClick={() => { onClear(); setShowLifecycle(false); }}
            className="ml-auto px-2 py-1 rounded text-[9px] font-mono text-forge-muted
              border border-forge-border hover:text-forge-text transition-colors">
            clear
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {showLifecycle && selectedDef ? (
          <ReactAgentCanvas
            definition={selectedDef}
            allDefinitions={definitions}
            runtimeStatus={runtimeMap.get(selectedDef.role)?.status}
          />
        ) : (
          <PhaseGapSelector
            selectedGap={selectedGap}
            selectedRole={selectedRole}
            onGapClick={onGapClick}
            onRoleClick={onRoleClick}
          />
        )}
      </div>
    </div>
  );

  const promptPanel = (
    <div className="h-full min-h-0 bg-forge-surface border-t border-forge-border flex flex-col">
      {promptTarget && <PromptContextStrip target={promptTarget} />}
      <div className="flex-1 min-h-0">
        {promptTarget
          ? <PromptEditor target={promptTarget} />
          : <PromptEditorPlaceholder />
        }
      </div>
    </div>
  );

  return (
    <div className="h-full min-h-0 p-3 pl-1.5">
      <div className="h-full min-h-0 rounded-xl border border-forge-border overflow-hidden">
        <ResizableSplitV
          top={canvas}
          bottom={promptPanel}
          initialSplit={62}
          minTop={25}
          maxTop={85}
          storageKey="agent-inspector-right"
        />
      </div>
    </div>
  );
}

// ── Dashboard root ─────────────────────────────────────────────────────────────

export function AgentInspector() {
  const { agents: liveAgents } = useStore();
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [selectedGap,  setSelectedGap]  = useState<string | null>(null);

  const { data: definitions = [], isLoading, refetch } = useQuery<AgentDefinition[]>({
    queryKey: ['agent-definitions'],
    queryFn: async () => {
      const r = await fetch('/api/agents/definitions');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    staleTime: 60_000,
  });

  const { data: runtimeStates = [] } = useQuery<ApiAgentState[]>({
    queryKey: ['agent-runtime-states'],
    queryFn: async () => {
      const r = await fetch('/api/agents');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 8_000,
  });

  const runtimeMap = new Map(runtimeStates.map(s => [s.display_name, s]));

  const resolvedStatus = (role: string): string => {
    const live = Object.values(liveAgents).find(a => a.role === role);
    return live?.status ?? runtimeMap.get(role)?.status ?? 'IDLE';
  };

  // Gap chip click → set gap, clear role
  const handleGapClick = (gap: string) => {
    setSelectedGap(prev => prev === gap ? null : gap);
    setSelectedRole(null);
  };

  // Agent role click (card or chip row) → set role, clear gap
  const handleRoleClick = (role: string) => {
    setSelectedRole(prev => prev === role ? null : role);
    setSelectedGap(null);
  };

  const handleClear = () => {
    setSelectedRole(null);
    setSelectedGap(null);
  };

  return (
    <div className="h-full flex flex-col animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-forge-border shrink-0">
        <div className="flex items-center gap-3">
          <Bot size={20} className="text-forge-accent" />
          <div>
            <h1 className="text-xl font-bold font-mono text-forge-text">Agent Inspector</h1>
            <p className="text-xs text-forge-muted font-mono">
              {definitions.length} agents · select a gap chip or agent role to edit its prompt
            </p>
          </div>
        </div>
        <button onClick={() => refetch()}
          className="p-2 rounded-lg bg-forge-surface border border-forge-border
            text-forge-muted hover:text-forge-text transition-colors"
          title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0">
        <ResizableSplit
          initialSplit={35}
          minLeft={22}
          maxLeft={55}
          storageKey="agent-inspector"
          left={
            <div className="flex flex-col min-h-0 h-full p-3 pr-1.5 overflow-y-auto">
              {isLoading
                ? <p className="text-forge-muted text-sm font-mono p-4">Loading agents…</p>
                : (
                  <div className="space-y-2">
                    {definitions.map(def => {
                      const rt = runtimeMap.get(def.role);
                      const merged: ApiAgentState | undefined = rt
                        ? { ...rt, status: resolvedStatus(def.role) }
                        : undefined;
                      return (
                        <AgentCard
                          key={def.role}
                          def={def}
                          runtime={merged}
                          selected={selectedRole === def.role}
                          onClick={() => handleRoleClick(def.role)}
                        />
                      );
                    })}
                  </div>
                )}
            </div>
          }
          right={
            <RightPanel
              definitions={definitions}
              runtimeMap={runtimeMap}
              selectedRole={selectedRole}
              selectedGap={selectedGap}
              onRoleClick={handleRoleClick}
              onGapClick={handleGapClick}
              onClear={handleClear}
            />
          }
        />
      </div>
    </div>
  );
}
