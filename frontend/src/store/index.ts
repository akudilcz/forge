import { create } from 'zustand';
import { combine } from 'zustand/middleware';
import { queryClient } from '@/queryClient';
import { nextPhaseTimes, type PhaseTimes } from '@/lib/phaseTiming';

export interface WorkQueueItem {
  id: string;
  phase: number;
  urgency: 'critical' | 'high' | 'medium' | 'low';
  importance: 'high' | 'medium' | 'low';
  category: string;
  description: string;
  target: string;
  affected_files: string[];
  effort: 'low' | 'medium' | 'high';
  rationale: string;
  status: 'pending' | 'in_progress' | 'done' | 'failed';
}

export interface WorkQueueAction {
  round: number;
  work_item_id: string;
  phase: number;
  category: string;
  files_modified: string[];
  tool_calls: number;
  gap_count_before: number;
  gap_count_after: number;
  outcome: 'improved' | 'no_change' | 'worse';
  summary: string;
}

export interface LogEntry {
  ts: string;           // HH:mm:ss.SSS
  level: 'INFO' | 'WARN' | 'ERROR';
  cat: string;          // LOOP | PHASE | GAP | AGENT | LLM | TOOL | USER | SYS
  msg: string;
  detail?: string;
  /** LLM / tool telemetry forwarded from FORGE_LOG payloads when present. */
  model?: string;
  durationMs?: number;
  promptTokens?: number;
  completionTokens?: number;
  toolName?: string;
}

// Types for the v4 Graph-Centric Architecture
export type GapType =
  | 'UNCHUNKED_DOCUMENT' | 'UNCOVERED_PARA'
  | 'UNARCHITECTED' | 'UNMODULARISED' | 'UNCONTRACTED'
  | 'UNREFINED_HLR' | 'UNDESIGNED' | 'UNSYNCED_DESIGN' | 'UNSYNCED_TEST'
  | 'UNSUITED' | 'UNTESTED_HLR' | 'UNTESTED_LLR'
  | 'STALE_NODE' | 'ORPHAN_NODE' | 'EMPTY_CONTENT' | 'STALE_TRACE_TO'
  | 'INCONSISTENT_CONTENT' | 'MALFORMED_REQUIREMENT' | 'UNTITLED_NODE'
  | 'DUPLICATE_NODE'
  | 'NON_ATOMIC_REQUIREMENT' | 'NON_EARS_REQUIREMENT'
  | 'UNTESTED_CODE' | 'UNEXECUTED_CASE';

export interface Gap {
  type: GapType;
  priority: number;
  node_id: string;
  description: string;
  context?: Record<string, unknown>;
}

export interface AgentState {
  id: string;
  role: string;
  status: 'IDLE' | 'WORKING' | 'THINKING';
  currentGapId: string | null;
  currentAction: string | null;
}

export type PhaseStatus = 'pending' | 'active' | 'complete' | 'awaiting_approval' | 'skipped';

export interface SessionState {
  loopStatus: 'IDLE' | 'RUNNING' | 'STOPPING';
  iterationCount: number;
}

// Initial State
const initialState = {
  session: {
    loopStatus: 'IDLE',
    iterationCount: 0,
  } as SessionState,
  phases: {} as Record<number, { status: PhaseStatus }>,
  /** Client-side wall clock per phase, driven by status transitions. */
  phaseTimes: {} as PhaseTimes,
  gaps: [] as Gap[],
  resolvedGaps: [] as Gap[],
  agents: {} as Record<string, AgentState>,
  logs: [] as LogEntry[],
  consoleOpen: true,
  lastLlmModel: '' as string,
  lastLlmSeq: 0,
  llmBusy: false,
  lastCtxTokens: 0,
  lastCtxWindow: 0,
  lineCoverage: null as number | null,
  branchCoverage: null as number | null,
  workQueue: [] as WorkQueueItem[],
  workQueueHistory: [] as WorkQueueAction[],
  /** Files recently written by the agent — path → expiry timestamp (ms). */
  recentlyEditedFiles: {} as Record<string, number>,
};

export const useStore = create(
  combine(initialState, (set, _get) => {
    // Internal log helpers — avoids recursive get() typing issue with combine()
    const _log = (message: string) => {
      const now = new Date();
      const ts = now.toTimeString().slice(0, 8) + '.' + String(now.getMilliseconds()).padStart(3, '0');
      const entry: LogEntry = { ts, level: 'INFO', cat: 'SYS', msg: message };
      set((s) => ({ logs: [entry, ...s.logs].slice(0, 5000) }));
    };
    const _logEntry = (entry: LogEntry) => {
      set((s) => {
        const next: Partial<typeof initialState> = { logs: [entry, ...s.logs].slice(0, 5000) };
        // Track file edits from TOOL log entries (file_write, file_patch)
        if (entry.cat === 'TOOL' && entry.msg) {
          const m = entry.msg.match(/^(?:file_write|file_patch|multi_file_write)\s+\[ok\].*?→.*?((?:src|tests)\/\S+\.py)/);
          if (m) {
            const expiry = Date.now() + 5000;
            next.recentlyEditedFiles = { ...s.recentlyEditedFiles, [m[1]]: expiry };
            // Schedule cleanup
            setTimeout(() => {
              set((ss) => {
                const updated = { ...ss.recentlyEditedFiles };
                if (updated[m[1]] && updated[m[1]] <= Date.now()) delete updated[m[1]];
                return { recentlyEditedFiles: updated };
              });
            }, 5100);
          }
        }
        return next;
      });
    };
    const _logUser = (message: string) => {
      const now = new Date();
      const ts = now.toTimeString().slice(0, 8) + '.' + String(now.getMilliseconds()).padStart(3, '0');
      _logEntry({ ts, level: 'INFO', cat: 'USER', msg: message });
    };

    return ({
    // Session Actions
    startLoop: async (opts?: { start_phase?: number; end_phase?: number }) => {
      _logUser(`Start loop (phases ${opts?.start_phase ?? 0}–${opts?.end_phase ?? 13})`);
      set((s) => ({ session: { ...s.session, loopStatus: 'RUNNING' } }));
      try {
        const res = await fetch('/api/phases/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            start_phase: opts?.start_phase ?? 0,
            end_phase: opts?.end_phase ?? 13,
          }),
        });
        if (!res.ok) {
          set((s) => ({ session: { ...s.session, loopStatus: 'IDLE' } }));
          _log(`Start failed: ${res.status} ${res.statusText}`);
        }
      } catch (err) {
        set((s) => ({ session: { ...s.session, loopStatus: 'IDLE' } }));
        _log(`Start failed: ${String(err)}`);
      }
    },
    stopLoop: async () => {
      _logUser('Stop loop');
      set((s) => ({ session: { ...s.session, loopStatus: 'STOPPING' } }));
      try {
        await fetch('/api/phases/stop', { method: 'POST' });
      } catch (err) {
        _log(`Stop failed: ${String(err)}`);
      } finally {
        set((s) => ({ session: { ...s.session, loopStatus: 'IDLE' } }));
      }
    },
    resetBuild: async () => {
      _logUser('Reset build');
      try {
        const res = await fetch('/api/phases/reset', { method: 'POST' });
        if (!res.ok) {
          _log(`Reset failed: ${res.status} ${res.statusText}`);
          return;
        }
      } catch (err) {
        _log(`Reset failed: ${String(err)}`);
        return;
      }
      set({
        phases: Object.fromEntries(
          Array.from({ length: 15 }, (_, i) => [i, { status: 'pending' as PhaseStatus }])
        ),
        phaseTimes: {},
        gaps: [],
        resolvedGaps: [],
        agents: {},
        session: { loopStatus: 'IDLE', iterationCount: 0 },
      });
      // Invalidate all TanStack Query caches so dashboards refetch fresh data
      queryClient.invalidateQueries();
    },
    // Purge all derived nodes (PARA, HLR, …) and reset phases 2-13 to pending.
    // Called after the whitepaper is edited so the pipeline re-runs from scratch.
    purgeDerived: async () => {
      _logUser('Purge derived nodes');
      try {
        await fetch('/api/phases/purge-derived', { method: 'POST' });
      } catch (err) {
        _log(`Purge failed: ${String(err)}`);
        return;
      }
      set((s) => ({
        phases: Object.fromEntries(
          Array.from({ length: 15 }, (_, i) => [
            i,
            { status: (i < 2 ? s.phases[i]?.status : 'pending') as PhaseStatus },
          ])
        ),
        phaseTimes: Object.fromEntries(
          Object.entries(s.phaseTimes).filter(([k]) => parseInt(k, 10) < 2),
        ),
        gaps: [],
        resolvedGaps: [],
        agents: {},
      }));
      queryClient.invalidateQueries();
    },
    // Kick off Quality Auditor consistency checks on all nodes produced by a phase.
    qualCheck: async (phase: number) => {
      _logUser(`Quality check phase ${phase}`);
      try {
        await fetch(`/api/phases/${phase}/qual-check`, { method: 'POST' });
      } catch (err) {
        _log(`Qual check failed: ${String(err)}`);
      }
    },
    // Run gap analyser for a phase and broadcast structural gaps (no agents).
    scanGaps: async (phase: number) => {
      _logUser(`Scan gaps phase ${phase}`);
      try {
        await fetch(`/api/phases/${phase}/scan`, { method: 'POST' });
      } catch (err) {
        _log(`Gap scan failed: ${String(err)}`);
      }
    },
    // Run quality gap analysis for a phase's nodes and broadcast (no agents).
    scanQual: async (phase: number) => {
      _logUser(`Quality scan phase ${phase}`);
      try {
        await fetch(`/api/phases/${phase}/scan-qual`, { method: 'POST' });
      } catch (err) {
        _log(`Qual scan failed: ${String(err)}`);
      }
    },
    setLoopStatus: (status: SessionState['loopStatus']) =>
      set((s) => ({ session: { ...s.session, loopStatus: status } })),
    setIterationCount: (count: number) =>
      set((s) => ({ session: { ...s.session, iterationCount: count } })),

    // Phase Actions
    updatePhase: (phase: number, status: PhaseStatus) =>
      set((s) => ({
        phases: { ...s.phases, [phase]: { status } },
        phaseTimes: nextPhaseTimes(s.phaseTimes, phase, status, Date.now()),
      })),
    setPhases: (phases: Array<{ phase_number: number; status: PhaseStatus }>) =>
      set((s) => ({
        phases: Object.fromEntries(phases.map((p) => [p.phase_number, { status: p.status as PhaseStatus }])),
        phaseTimes: phases.reduce(
          (acc, p) => nextPhaseTimes(acc, p.phase_number, p.status as PhaseStatus, Date.now()),
          s.phaseTimes,
        ),
      })),

    // Gap Actions
    setGaps: (gaps: Gap[]) => set({ gaps }),
    // Merge incoming gaps:
    // - keep existing gaps still present in the incoming list
    // - move removed gaps into resolvedGaps (capped at 50)
    // - append genuinely new gaps
    mergeGaps: (incoming: Gap[]) => set((s) => {
      const incomingKeys = new Set(incoming.map(g => `${g.type}:${g.node_id}`));
      const retained = s.gaps.filter(g => incomingKeys.has(`${g.type}:${g.node_id}`));
      const justResolved = s.gaps.filter(g => !incomingKeys.has(`${g.type}:${g.node_id}`));
      const retainedKeys = new Set(retained.map(g => `${g.type}:${g.node_id}`));
      const newGaps = incoming.filter(g => !retainedKeys.has(`${g.type}:${g.node_id}`));
      const resolvedGaps = [...justResolved, ...s.resolvedGaps].slice(0, 50);
      return { gaps: [...retained, ...newGaps], resolvedGaps };
    }),
    resolveGap: (gapId: string) =>
      set((s) => ({ gaps: s.gaps.filter((g) => g.node_id !== gapId) })),

    // Agent Actions
    updateAgent: (agent: AgentState) =>
      set((s) => ({ agents: { ...s.agents, [agent.id]: agent } })),
    clearAgents: () =>
      set((s) => ({
        agents: Object.fromEntries(
          Object.entries(s.agents).map(([k, a]) => [k, { ...a, status: 'IDLE' as const }]),
        ),
      })),

    // Log Actions
    addLog: (message: string) => _log(message),
    addLogEntry: (entry: LogEntry) => _logEntry(entry),
    setLastLlmModel: (model: string) => set((s) => ({ lastLlmModel: model, lastLlmSeq: s.lastLlmSeq + 1, llmBusy: true })),
    setLlmDone: () => set({ llmBusy: false }),
    setLastCtx: (tokens: number, window: number) => set({ lastCtxTokens: tokens, lastCtxWindow: window }),
    setLineCoverage: (pct: number) => set({ lineCoverage: pct }),
    setBranchCoverage: (pct: number) => set({ branchCoverage: pct }),
    clearLogs: async () => {
      _logUser('Clear logs');
      set({ logs: [] });
      try {
        await fetch('/api/console/clear', { method: 'POST' });
      } catch {
        // best-effort — UI is already cleared
      }
    },

    // Console Actions
    toggleConsole: () => set((s) => { _logUser(s.consoleOpen ? 'Collapse console' : 'Expand console'); return { consoleOpen: !s.consoleOpen }; }),
    runConsole: async (request: string) => {
      const now = new Date();
      const ts = now.toTimeString().slice(0, 8) + '.' + String(now.getMilliseconds()).padStart(3, '0');
      _logEntry({ ts, level: 'INFO', cat: 'CONS', msg: `> ${request}` });
      set({ consoleOpen: true });
      try {
        await fetch('/api/console/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request }),
        });
      } catch (err) {
        const now2 = new Date();
        const ts2 = now2.toTimeString().slice(0, 8) + '.' + String(now2.getMilliseconds()).padStart(3, '0');
        _logEntry({ ts: ts2, level: 'ERROR', cat: 'CONS', msg: `error: ${String(err)}` });
      }
    },
    clearConversation: async () => {
      _logUser('Clear conversation history');
      try {
        await fetch('/api/console/clear', { method: 'POST' });
        _log('Conversation history cleared');
      } catch (err) {
        _log(`Clear conversation failed: ${String(err)}`);
      }
    },
    // Work Queue Actions
    setWorkQueue: (items: WorkQueueItem[], history: WorkQueueAction[]) =>
      set({ workQueue: items, workQueueHistory: history }),

    // Exported for components to log UI interactions directly
    logUserAction: (message: string) => _logUser(message),
  })})
);
