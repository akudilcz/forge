/** NodeBadge — compact chip/badge for a graph node type prefix. */

interface NodeBadgeProps {
  nodeType: string; // e.g. "req:func:auth-login" or just "req"
  label?: string;
  className?: string;
}

const TYPE_STYLES: Record<string, string> = {
  req: 'bg-forge-accent/15 text-forge-accent border-forge-accent/30',
  tst: 'bg-forge-success/15 text-forge-success border-forge-success/30',
  impl: 'bg-forge-purple/15 text-forge-purple border-forge-purple/30',
  arch: 'bg-forge-warning/15 text-forge-warning border-forge-warning/30',
  sec: 'bg-forge-error/15 text-forge-error border-forge-error/30',
  rev: 'bg-forge-orange/15 text-forge-orange border-forge-orange/30',
  des: 'bg-forge-muted/15 text-forge-muted border-forge-muted/30',
  cov: 'bg-forge-success/10 text-forge-success border-forge-success/20',
};

function extractPrefix(nodeType: string): string {
  return nodeType.split(':')[0] ?? nodeType;
}

export function NodeBadge({ nodeType, label, className = '' }: NodeBadgeProps) {
  const prefix = extractPrefix(nodeType);
  const style = TYPE_STYLES[prefix] ?? 'bg-forge-border/20 text-forge-muted border-forge-border/30';
  const display = label ?? prefix.toUpperCase();

  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${style} ${className}`}
      title={nodeType}
    >
      {display}
    </span>
  );
}
