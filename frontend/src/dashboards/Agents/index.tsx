/**
 * Agents dashboard — agent status cards on the left,
 * RECORD node browser on the right.
 */
import { useQuery } from '@tanstack/react-query';
import { useStore } from '@/store';
import { Bot, Activity, FileText } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';
import { ResizableSplit } from '@/components/ResizableSplit';

const RECORD_TYPES = new Set(['RECORD']);

// ── Agent card ─────────────────────────────────────────────────────────────────

function AgentCard({ agent }: { agent: { id: string; role: string; status: string; currentAction: string | null; currentGapId: string | null } }) {
  return (
    <div className="p-4 rounded-xl border border-forge-border bg-forge-surface hover:border-forge-accent/40 transition-all group">
      <div className="flex items-center justify-between mb-3">
        <div className="w-9 h-9 rounded-lg bg-forge-accent/10 flex items-center justify-center border border-forge-accent/20 group-hover:bg-forge-accent/20 transition-colors">
          <Bot className="text-forge-accent" size={18} />
        </div>
        <div className={`px-2 py-1 rounded text-[10px] font-mono uppercase tracking-wider border ${
          agent.status === 'WORKING'  ? 'text-forge-accent border-forge-accent/30 bg-forge-accent/5 animate-pulse' :
          agent.status === 'THINKING' ? 'text-forge-warning border-forge-warning/30 bg-forge-warning/5' :
          'text-forge-success border-forge-success/30 bg-forge-success/5'
        }`}>
          {agent.status}
        </div>
      </div>

      <h3 className="font-bold text-forge-text text-sm mb-0.5">{agent.role}</h3>
      <p className="text-[10px] font-mono text-forge-muted mb-3">{agent.id}</p>

      <div className="space-y-2">
        <div>
          <span className="text-[9px] font-mono text-forge-muted uppercase tracking-widest">Active Task</span>
          <p className="text-xs font-mono text-forge-text line-clamp-2 min-h-[2rem] mt-0.5">
            {agent.currentAction || 'Waiting for dispatch…'}
          </p>
        </div>

        {agent.currentGapId && (
          <div className="flex items-center gap-2 text-[10px] font-mono text-forge-accent bg-forge-accent/10 p-2 rounded border border-forge-accent/20">
            <Activity size={11} />
            <span className="truncate">{agent.currentGapId}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main dashboard ─────────────────────────────────────────────────────────────

export function Agents() {
  const { agents } = useStore();
  const agentList = Object.values(agents);

  const { data: allNodes = [], isLoading: nodesLoading, refetch } = useQuery<GNode[]>({
    queryKey: ['graph-nodes'],
    queryFn: async () => {
      const r = await fetch('/api/graph/nodes');
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data) ? data : [];
    },
    refetchInterval: 15_000,
  });

  const recordNodes = allNodes.filter(n => RECORD_TYPES.has(n.node_type));

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      {/* Header */}
      <div className="shrink-0">
        <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Agent Pool</h1>
        <p className="text-sm text-forge-muted font-mono">
          Specialized AI workers monitoring and evolving the Project Graph.
          {agentList.length > 0 && (
            <span className="ml-2 text-forge-accent">{agentList.length} agents</span>
          )}
        </p>
      </div>

      {/* Two-panel body — NTP left (graph-first), agent cards right */}
      <div className="flex-1 min-h-0">
        <ResizableSplit
          initialSplit={58}
          minLeft={20}
          maxLeft={80}
          storageKey="agents"
          left={
            <div className="flex flex-col min-h-0 h-full pr-2">
              <NodeTablePanel
                nodes={recordNodes}
                isLoading={nodesLoading}
                title="Agent Records"
                icon={<FileText size={14} />}
                onRefresh={refetch}
                emptyMessage="No RECORD nodes yet. Records are created by agents during integration and review phases."
                extraDetail={(node) => <NodeContextPanel node={node} />}
              />
            </div>
          }
          right={
            <div className="flex flex-col min-h-0 h-full pl-2 overflow-y-auto">
              <div className="grid grid-cols-1 gap-3">
                {agentList.map(agent => (
                  <AgentCard key={agent.id} agent={agent} />
                ))}
                {agentList.length === 0 && (
                  <div className="py-12 text-center border-2 border-dashed border-forge-border rounded-xl text-forge-muted font-mono text-sm">
                    No agents initialized in the pool.
                  </div>
                )}
              </div>
            </div>
          }
        />
      </div>
    </div>
  );
}
