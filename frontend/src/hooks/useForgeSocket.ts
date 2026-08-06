import { useEffect, useRef } from 'react';
import { useStore, type AgentState, type Gap, type LogEntry, type PhaseStatus, type WorkQueueItem, type WorkQueueAction } from '@/store';
import { queryClient } from '@/queryClient';

export function useForgeSocket() {
  const socketRef = useRef<WebSocket | null>(null);
  const {
    updateAgent, clearAgents, mergeGaps, addLog, addLogEntry, setLastLlmModel, setLlmDone, setLastCtx, setLineCoverage, setBranchCoverage,
    updatePhase, setLoopStatus, setIterationCount, setPhases, setWorkQueue,
  } = useStore();

  useEffect(() => {
    let destroyed = false;
    let retryDelay = 1000;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (destroyed) return;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
      socketRef.current = socket;

      socket.onopen = () => {
        retryDelay = 1000;
      };

      socket.onclose = () => {
        if (!destroyed) {
          retryTimer = setTimeout(connect, retryDelay);
          retryDelay = Math.min(retryDelay * 2, 30_000);
        }
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data) as {
          event_type: string;
          payload: Record<string, unknown>;
        };

        switch (data.event_type) {
          case 'AGENT_STATUS_CHANGE': {
            const p = data.payload;
            updateAgent({
              id: p.agent_id as string,
              role: p.agent_id as string,
              status: p.status === 'running' ? 'WORKING' : 'IDLE',
              currentGapId: (p.current_node as string | null) ?? null,
              currentAction: (p.current_node as string | null) ?? null,
            } as AgentState);
            addLog(`Agent ${p.agent_id as string}: ${p.status as string}`);
            break;
          }

          case 'GAP_LIST_UPDATE': {
            const gaps = (data.payload.gaps as Gap[]) ?? [];
            mergeGaps(gaps);
            // Graph state changed — refresh all dashboard data
            queryClient.invalidateQueries();
            break;
          }

          case 'PHASE_TRANSITION': {
            const p = data.payload;
            if (p.to_phase !== undefined) {
              updatePhase(p.to_phase as number, p.status as PhaseStatus);
              addLog(`Phase ${p.to_phase as number}: ${p.status as string}`);
            }
            if (p.loop_status === 'running') {
              setLoopStatus('RUNNING');
            } else if (p.loop_status === 'complete' || p.loop_status === 'idle') {
              setLoopStatus('IDLE');
              clearAgents();
              addLog(`Build ${p.loop_status as string}`);
            } else if (p.loop_status === 'error') {
              setLoopStatus('IDLE');
              clearAgents();
              addLog('Build error — check server logs');
            }
            // Phase state changed — refresh dashboards
            queryClient.invalidateQueries();
            break;
          }

          case 'TASK_START': {
            const p = data.payload;
            addLog(`→ ${p.description as string}`);
            break;
          }

          case 'TASK_COMPLETE': {
            const p = data.payload;
            addLog(`✓ agent=${p.agent_id as string} success=${p.success}`);
            break;
          }

          case 'SESSION_SNAPSHOT': {
            const rawAgents = (data.payload.agents as Array<Record<string, unknown>>) ?? [];
            for (const a of rawAgents) {
              const backendStatus = (a.status as string) ?? 'idle';
              updateAgent({
                id: a.agent_id as string,
                role: (a.display_name as string) ?? (a.agent_id as string),
                status: backendStatus === 'running' ? 'WORKING' : 'IDLE',
                currentGapId: (a.current_task as string | null) ?? null,
                currentAction: (a.current_task as string | null) ?? null,
              } as AgentState);
            }
            if (typeof data.payload.iteration_count === 'number') {
              setIterationCount(data.payload.iteration_count as number);
            }
            const rawPhases = data.payload.phases as Array<{ phase_number: number; status: PhaseStatus }> | undefined;
            if (rawPhases && rawPhases.length > 0) {
              setPhases(rawPhases);
            }
            const snapLoopStatus = data.payload.loop_status as string | undefined;
            if (snapLoopStatus === 'running') {
              setLoopStatus('RUNNING');
            } else if (snapLoopStatus) {
              setLoopStatus('IDLE');
            }
            break;
          }

          case 'FORGE_LOG': {
            const p = data.payload;
            const numMeta = (v: unknown): number | undefined => {
              if (v == null) return undefined;
              const n = typeof v === 'number' ? v : parseInt(String(v), 10);
              return Number.isFinite(n) ? n : undefined;
            };
            addLogEntry({
              ts: p.ts as string,
              level: (p.level as LogEntry['level']) ?? 'INFO',
              cat: (p.cat as string) ?? 'SYS',
              msg: p.msg as string,
              detail: (p.detail as string | undefined) ?? undefined,
              model: (p.model as string | undefined) ?? undefined,
              durationMs: numMeta(p.duration_ms),
              promptTokens: numMeta(p.prompt_tokens),
              completionTokens: numMeta(p.completion_tokens),
              toolName: (p.tool_name as string | undefined) ?? undefined,
            });
            // Update last LLM model and context usage if present in payload
            if (p.model) setLastLlmModel(p.model as string);
            if (p.prompt_tokens && p.context_window) {
              setLastCtx(parseInt(p.prompt_tokens as string, 10), parseInt(p.context_window as string, 10));
            }
            // Clear LLM busy when response arrives (← in LLM/AGNT, or crew finish)
            {
              const logMsg = (p.msg as string) ?? '';
              const logCat = ((p.cat as string) ?? '').trim();
              if (
                (logCat === 'LLM' && logMsg.startsWith('←')) ||
                (logCat === 'AGNT' && logMsg.startsWith('LLM →')) ||
                (logCat === 'CREW' && logMsg.startsWith('finish:'))
              ) {
                setLlmDone();
              }
            }
            // Update line coverage from workspace scanner
            if (p.line_coverage) setLineCoverage(parseFloat(p.line_coverage as string));
            if (p.branch_coverage) setBranchCoverage(parseFloat(p.branch_coverage as string));
            // Trigger immediate counter refresh when a graph write succeeds,
            // before GAP_LIST_UPDATE arrives (which only fires after full dispatch).
            const msg = (p.msg as string) ?? '';
            const cat = ((p.cat as string) ?? '').trim();
            if (
              cat === 'TOOL' &&
              (msg.startsWith('graph_write [ok]') || msg.startsWith('multi_graph_write [ok]'))
            ) {
              queryClient.invalidateQueries({ queryKey: ['graph-nodes'] });
            }
            // Refresh dashboard when code gen persists traces or dashboard renders docs
            if (
              (cat === 'CGEN' && msg.includes('traces_persisted')) ||
              (cat === 'DASH' && msg.includes('rendered'))
            ) {
              queryClient.invalidateQueries({ queryKey: ['graph-nodes'] });
              queryClient.invalidateQueries({ queryKey: ['workspace-tree'] });
              queryClient.invalidateQueries({ queryKey: ['workspace-doc'] });
            }
            break;
          }

          case 'LOG_ENTRY': {
            const msg = (data.payload.message as string | undefined)
              ?? JSON.stringify(data.payload);
            addLog(msg);
            break;
          }

          case 'AUDIT_ENTRY': {
            const entry = data.payload.entry as Record<string, unknown> | undefined;
            if (entry?.message) addLog(entry.message as string);
            break;
          }

          case 'WORK_QUEUE': {
            const items = (data.payload.items as WorkQueueItem[]) ?? [];
            const history = (data.payload.history as WorkQueueAction[]) ?? [];
            setWorkQueue(items, history);
            break;
          }

          case 'GRAPH_NODE_CHANGED': {
            queryClient.invalidateQueries({ queryKey: ['graph-all-nodes'] });
            break;
          }
        }
      };
    }

    // Defer by one tick so React StrictMode's synchronous mount→cleanup→mount
    // cycle cancels the first connection attempt before it opens, avoiding
    // a "write EPIPE" on Vite's WS proxy when the backend sends SESSION_SNAPSHOT
    // to the socket that StrictMode already closed.
    const initTimer = setTimeout(() => {
      if (!destroyed) connect();
    }, 0);

    return () => {
      destroyed = true;
      clearTimeout(initTimer);
      if (retryTimer) clearTimeout(retryTimer);
      socketRef.current?.close();
    };
  }, []);

  return socketRef.current;
}
