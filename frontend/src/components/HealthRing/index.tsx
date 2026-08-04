/** HealthRing — circular SVG progress ring for a 0–1 health score. */

interface HealthRingProps {
  score: number; // 0.0 – 1.0
  size?: number;
  strokeWidth?: number;
  label?: string;
}

function scoreColour(score: number): string {
  if (score >= 0.75) return '#22c55e'; // green
  if (score >= 0.45) return '#f59e0b'; // amber
  return '#ef4444'; // red
}

export function HealthRing({ score, size = 48, strokeWidth = 4, label }: HealthRingProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(1, score)) * circumference;
  const colour = scoreColour(score);
  const pct = Math.round(score * 100);

  return (
    <div className="flex flex-col items-center gap-1" title={label}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-forge-border"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={colour}
          strokeWidth={strokeWidth}
          strokeDasharray={`${filled} ${circumference}`}
          strokeLinecap="round"
        />
      </svg>
      <span className="text-xs font-mono" style={{ color: colour }}>
        {pct}%
      </span>
      {label && <span className="text-xs font-mono text-forge-muted">{label}</span>}
    </div>
  );
}
