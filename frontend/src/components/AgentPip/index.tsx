/** AgentPip — small colored status indicator dot for an agent. */

interface AgentPipProps {
  status: 'idle' | 'working' | 'blocked' | 'done' | 'error' | string;
  size?: number;
  title?: string;
}

const STATUS_COLOUR: Record<string, string> = {
  idle: 'bg-forge-muted',
  working: 'bg-forge-accent animate-pulse',
  blocked: 'bg-forge-warning',
  done: 'bg-forge-success',
  error: 'bg-forge-error',
};

export function AgentPip({ status, size = 8, title }: AgentPipProps) {
  const colour = STATUS_COLOUR[status] ?? 'bg-forge-muted';
  return (
    <span
      className={`inline-block rounded-full flex-shrink-0 ${colour}`}
      style={{ width: size, height: size }}
      title={title ?? status}
    />
  );
}
