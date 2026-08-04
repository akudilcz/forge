/**
 * PhaseLifecycleCanvas — SVG diagram of the Run Phase lifecycle.
 *
 * Layout (top-to-bottom):
 *   START
 *     ↓
 *   Identify Phase Gaps   (GapAnalyser, no agents)
 *     ↓
 *   Close Phase Gaps      (structural agent dispatched per gap)
 *     ↓
 *   Identify Quality Gaps (LLM detect-only, no agents)
 *     ↓
 *   Close Quality Gaps    (Quality Auditor dispatched per gap)
 *     ↓
 *   END
 */
import { AGENT_COLOR, type AgentDefinition } from './constants';

const CW   = 500;
const CX   = CW / 2;
const NW   = 360;
const NX   = CX - NW / 2;
const V    = 38;   // vertical gap between nodes

const START_Y  = 24;
const START_H  = 32;
const SCAN1_Y  = START_Y  + START_H  + V;
const SCAN1_H  = 52;
const CLOSE1_Y = SCAN1_Y  + SCAN1_H  + V;
const CLOSE1_H = 78;
const SCAN2_Y  = CLOSE1_Y + CLOSE1_H + V;
const SCAN2_H  = 52;
const CLOSE2_Y = SCAN2_Y  + SCAN2_H  + V;
const CLOSE2_H = 66;
const END_Y    = CLOSE2_Y + CLOSE2_H + V;
const END_H    = 32;
const END_W    = 100;
const END_X    = CX - END_W / 2;
const CH       = END_Y + END_H + 32;

// ── Primitives ────────────────────────────────────────────────────────────────

function Defs({ agentColor }: { agentColor: string }) {
  return (
    <defs>
      <marker id="arr-grey"   viewBox="0 0 8 8" refX="5" refY="4" markerWidth="5" markerHeight="5" orient="auto">
        <path d="M0 0 L8 4 L0 8z" fill="#6e7681" />
      </marker>
      <marker id="arr-amber"  viewBox="0 0 8 8" refX="5" refY="4" markerWidth="5" markerHeight="5" orient="auto">
        <path d="M0 0 L8 4 L0 8z" fill="#f59e0b" />
      </marker>
      <marker id="arr-agent"  viewBox="0 0 8 8" refX="5" refY="4" markerWidth="5" markerHeight="5" orient="auto">
        <path d="M0 0 L8 4 L0 8z" fill={agentColor} />
      </marker>
      <marker id="arr-violet" viewBox="0 0 8 8" refX="5" refY="4" markerWidth="5" markerHeight="5" orient="auto">
        <path d="M0 0 L8 4 L0 8z" fill="#a78bfa" />
      </marker>
    </defs>
  );
}

function CapsuleNode({ label, x, y, w, h, color }: {
  label: string; x: number; y: number; w: number; h: number; color: string;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={h / 2}
        fill={color + '25'} stroke={color} strokeWidth={1.5} />
      <text x={x + w / 2} y={y + h / 2 + 4} textAnchor="middle"
        fill={color} fontSize={11} fontFamily="monospace" fontWeight="600">
        {label}
      </text>
    </g>
  );
}

function StepNode({ x, y, w, h, color, title, line1, line2, active = false }: {
  x: number; y: number; w: number; h: number; color: string;
  title: string; line1?: string; line2?: string; active?: boolean;
}) {
  return (
    <>
      {/* header band */}
      <rect x={x} y={y} width={w} height={28} rx={6} fill={color + '35'} />
      <rect x={x} y={y + 22} width={w} height={6} fill={color + '35'} />
      {/* body */}
      <rect x={x} y={y + 28} width={w} height={h - 28} fill={color + '12'} />
      <rect x={x} y={y + h - 6} width={w} height={6} rx={6} fill={color + '12'} />
      {/* border */}
      <rect x={x} y={y} width={w} height={h} rx={6} fill="none"
        stroke={color} strokeWidth={1.4} />
      {/* title */}
      <text x={x + 14} y={y + 18} fill={color}
        fontSize={12} fontWeight="bold" fontFamily="monospace">{title}</text>
      {line1 && (
        <text x={x + 14} y={y + 43} fill={color + 'cc'}
          fontSize={10} fontFamily="monospace">{line1}</text>
      )}
      {line2 && (
        <text x={x + 14} y={y + 59} fill="#4e596988"
          fontSize={9.5} fontFamily="monospace">{line2}</text>
      )}
      {/* active pulse */}
      {active && (
        <rect x={x} y={y} width={w} height={h} rx={6}
          fill="none" stroke={color} strokeWidth={2.5} opacity={0.5}>
          <animate attributeName="opacity" values="0.5;0.1;0.5"
            dur="1.5s" repeatCount="indefinite" />
        </rect>
      )}
    </>
  );
}

function Arrow({ x1, y1, x2, y2, color, marker }: {
  x1: number; y1: number; x2: number; y2: number; color: string; marker: string;
}) {
  return (
    <line x1={x1} y1={y1} x2={x2} y2={y2}
      stroke={color} strokeWidth={1.5} markerEnd={`url(#${marker})`} />
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function ReactAgentCanvas({
  definition,
  allDefinitions = [],
  runtimeStatus,
}: {
  definition: AgentDefinition;
  allDefinitions?: AgentDefinition[];
  runtimeStatus?: string;
}) {
  const agentColor  = AGENT_COLOR[definition.role] ?? '#94a3b8';
  const isActive    = runtimeStatus === 'WORKING' || runtimeStatus === 'RUNNING';
  const isQualAgent = definition.role === 'Quality Auditor';

  // Find Quality Auditor definition for the Close Quality Gaps node
  const qualDef = allDefinitions.find(d => d.role === 'Quality Auditor') ?? definition;
  const qualColor = AGENT_COLOR['Quality Auditor'] ?? '#8b5cf6';

  return (
    <svg viewBox={`0 0 ${CW} ${CH}`} width="100%"
      style={{ background: '#0d1117', display: 'block' }}>
      <Defs agentColor={agentColor} />

      {/* START */}
      <CapsuleNode label="START" x={CX - 50} y={START_Y} w={100} h={START_H} color="#22c55e" />
      <Arrow x1={CX} y1={START_Y + START_H} x2={CX} y2={SCAN1_Y}
        color="#6e7681" marker="arr-grey" />

      {/* Identify Phase Gaps */}
      <StepNode x={NX} y={SCAN1_Y} w={NW} h={SCAN1_H} color="#f59e0b"
        title="Identify Phase Gaps"
        line1="GapAnalyser · structural gap detection · no agents" />
      <Arrow x1={CX} y1={SCAN1_Y + SCAN1_H} x2={CX} y2={CLOSE1_Y}
        color="#f59e0b" marker="arr-amber" />

      {/* Close Phase Gaps */}
      <StepNode x={NX} y={CLOSE1_Y} w={NW} h={CLOSE1_H} color={agentColor}
        title="Close Phase Gaps"
        line1={`${definition.role} · dispatched per structural gap`}
        line2={`model: ${definition.model} · ${definition.tools.length} tool(s)`}
        active={isActive && !isQualAgent} />
      <Arrow x1={CX} y1={CLOSE1_Y + CLOSE1_H} x2={CX} y2={SCAN2_Y}
        color={agentColor + 'bb'} marker="arr-agent" />

      {/* Identify Quality Gaps */}
      <StepNode x={NX} y={SCAN2_Y} w={NW} h={SCAN2_H} color="#a78bfa"
        title="Identify Quality Gaps"
        line1="LLM detect-only · consistency &amp; staleness checks" />
      <Arrow x1={CX} y1={SCAN2_Y + SCAN2_H} x2={CX} y2={CLOSE2_Y}
        color="#a78bfa" marker="arr-violet" />

      {/* Close Quality Gaps */}
      <StepNode x={NX} y={CLOSE2_Y} w={NW} h={CLOSE2_H} color={qualColor}
        title="Close Quality Gaps"
        line1={`${qualDef.role} · dispatched per quality gap`}
        line2={`model: ${qualDef.model} · ${qualDef.tools.length} tool(s)`}
        active={isActive && isQualAgent} />
      <Arrow x1={CX} y1={CLOSE2_Y + CLOSE2_H} x2={CX} y2={END_Y}
        color="#6e7681" marker="arr-grey" />

      {/* END */}
      <CapsuleNode label="END" x={END_X} y={END_Y} w={END_W} h={END_H} color="#ef4444" />
    </svg>
  );
}
