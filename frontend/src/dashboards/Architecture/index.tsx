import { useQuery } from '@tanstack/react-query';
import { Network } from 'lucide-react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import { NodeContextPanel } from '@/components/NodeContextPanel';

const ARCH_TYPES = new Set(['ARCHITECTURE', 'MODULE', 'CONTRACT']);

export function Architecture() {
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

  const archNodes = (allNodes ?? []).filter(n => ARCH_TYPES.has(n.node_type));

  const counts = archNodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.node_type] = (acc[n.node_type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="h-full flex flex-col p-6 gap-4 animate-fade-in">
      <div className="shrink-0">
        <h1 className="text-2xl font-bold font-mono text-forge-text mb-1">Architecture</h1>
        <p className="text-sm text-forge-muted font-mono">
          Architecture, modules and contracts.
          {archNodes.length > 0 && (
            <span className="ml-2 text-forge-accent">
              {Object.entries(counts).map(([t, c]) => `${c} ${t}`).join(' · ')}
            </span>
          )}
        </p>
      </div>

      <NodeTablePanel
        nodes={archNodes}
        isLoading={isLoading}
        title="Architecture Nodes"
        icon={<Network size={14} />}
        onRefresh={refetch}
        emptyMessage="No architecture nodes yet. Run phases 2–4 to generate ARCHITECTURE, MODULE and CONTRACT nodes."
        extraDetail={(node) => <NodeContextPanel node={node} />}
      />
    </div>
  );
}
