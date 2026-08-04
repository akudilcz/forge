import { Square } from 'lucide-react';
import { useStore } from '@/store';
import { AGENT_COLOR } from '@/dashboards/AgentInspector/constants';

export function StatusBar() {
  const { session, agents, stopLoop } = useStore();
  const activeAgents = Object.values(agents).filter(a => a.status !== 'IDLE');
  const isRunning = session.loopStatus === 'RUNNING';

  return (
    <header className="h-10 border-b border-forge-border bg-forge-surface flex items-center justify-between px-4 text-xs font-mono">
      {/* Active agent pills */}
      <div className="flex items-center gap-2">
        {activeAgents.map(agent => {
          const color = AGENT_COLOR[agent.role] ?? '#94a3b8';
          return (
            <span
              key={agent.id}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded border animate-pulse"
              style={{
                color,
                background: color + '15',
                borderColor: color + '40',
              }}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ background: color }}
              />
              {agent.role}
            </span>
          );
        })}
      </div>

      {/* Stop button */}
      <div className="flex items-center">
        {isRunning && (
          <button
            onClick={stopLoop}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-forge-error/40 bg-forge-error/10 text-forge-error hover:bg-forge-error/20 transition-colors"
            title="Stop the running loop"
          >
            <Square size={10} fill="currentColor" />
            Stop
          </button>
        )}
      </div>
    </header>
  );
}
