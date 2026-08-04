/**
 * nodeColors — single source of truth for node-type colors.
 *
 * One distinct color per v4 node type (14 types across 8 layers).
 * Colors follow the layer hierarchy — related types share a color family
 * to show their relationship while remaining visually distinct.
 *
 * TYPE_COLOR:  Tailwind class strings (text + bg + border) for UI badges.
 * TYPE_HEX:    Hex values for canvas / 3D graph rendering.
 */

// Minimal shape required to resolve a node's effective type key.
type NodeLike = { node_type: string; properties: Record<string, unknown> };

// ── Tailwind class strings ─────────────────────────────────────────────────────
// Layer  │ Node type     │ Color family
// ───────┼───────────────┼──────────────────
// L0     │ PROJECT       │ violet (root)
// L1     │ DOCUMENT      │ cyan
// L1     │ PARA          │ sky
// L2     │ HLR           │ amber (high-level)
// L2     │ LLR           │ orange (low-level)
// L3     │ ARCHITECTURE  │ rose
// L3     │ MODULE        │ blue
// L4     │ CONTRACT      │ purple
// L4     │ CLASS         │ indigo
// L5     │ SUITE         │ teal
// L5     │ CASE          │ green
// L6     │ TEST          │ emerald (test code)
// L6     │ RESULT        │ lime
// L7     │ RECORD        │ slate

export const TYPE_COLOR: Record<string, string> = {
  // L0
  PROJECT:      'text-violet-400 bg-violet-400/15 border-violet-400/30',
  // L1
  DOCUMENT:     'text-cyan-400 bg-cyan-400/15 border-cyan-400/30',
  PARA:         'text-sky-300 bg-sky-300/15 border-sky-300/30',
  // L2
  HLR:          'text-amber-400 bg-amber-400/15 border-amber-400/30',
  LLR:          'text-orange-400 bg-orange-400/15 border-orange-400/30',
  REQ:          'text-amber-400 bg-amber-400/15 border-amber-400/30',   // legacy fallback
  REQ_HLR:      'text-amber-400 bg-amber-400/15 border-amber-400/30',   // legacy split
  REQ_LLR:      'text-orange-400 bg-orange-400/15 border-orange-400/30', // legacy split
  // L3
  ARCHITECTURE: 'text-rose-400 bg-rose-400/15 border-rose-400/30',
  MODULE:       'text-blue-400 bg-blue-400/15 border-blue-400/30',
  // L4
  CONTRACT:     'text-purple-400 bg-purple-400/15 border-purple-400/30',
  CLASS:        'text-indigo-400 bg-indigo-400/15 border-indigo-400/30',
  // L5
  SUITE:        'text-teal-400 bg-teal-400/15 border-teal-400/30',
  CASE:         'text-green-400 bg-green-400/15 border-green-400/30',
  CASE_HLR:     'text-emerald-500 bg-emerald-500/15 border-emerald-500/30',
  CASE_LLR:     'text-green-400 bg-green-400/15 border-green-400/30',
  // L6
  TEST:         'text-emerald-400 bg-emerald-400/15 border-emerald-400/30',
  RESULT:       'text-lime-400 bg-lime-400/15 border-lime-400/30',
  // L7
  RECORD:       'text-slate-400 bg-slate-400/15 border-slate-400/30',
};

// ── Hex palette (3D graph / canvas rendering) ─────────────────────────────────

export const TYPE_HEX: Record<string, string> = {
  PROJECT:      '#7c3aed',   // violet-600
  DOCUMENT:     '#22d3ee',   // cyan-400
  PARA:         '#7dd3fc',   // sky-300
  HLR:          '#fbbf24',   // amber-400
  LLR:          '#f97316',   // orange-400
  REQ:          '#fbbf24',   // amber-400 (legacy)
  REQ_HLR:      '#fbbf24',   // amber-400 (legacy)
  REQ_LLR:      '#f97316',   // orange-400 (legacy)
  ARCHITECTURE: '#fb7185',   // rose-400
  MODULE:       '#60a5fa',   // blue-400
  CONTRACT:     '#a78bfa',   // violet-400
  CLASS:        '#818cf8',   // indigo-400
  SUITE:        '#2dd4bf',   // teal-400
  CASE:         '#4ade80',   // green-400
  CASE_HLR:     '#10b981',   // emerald-500
  CASE_LLR:     '#4ade80',   // green-400
  TEST:         '#34d399',   // emerald-400
  RESULT:       '#a3e635',   // lime-400
  RECORD:       '#94a3b8',   // slate-400
};

// ── Sort order — canonical ordering by layer for consistent UI presentation ───

export const TYPE_SORT_ORDER: Record<string, number> = {
  PROJECT: 0, DOCUMENT: 1, PARA: 2,
  HLR: 3, LLR: 4,
  ARCHITECTURE: 5, MODULE: 6,
  CONTRACT: 7, CLASS: 8,
  DESIGN: 9,
  SUITE: 10, CASE: 11, CASE_HLR: 12, CASE_LLR: 13,
  TEST: 14, RESULT: 15, RECORD: 16,
};

// ── Phase colors — one color per phase matching its produced node type ─────────

export const PHASE_COLOR: Record<number, string> = {
  0:  TYPE_HEX.PROJECT,
  1:  TYPE_HEX.DOCUMENT,
  2:  TYPE_HEX.PARA,
  3:  TYPE_HEX.HLR,
  4:  TYPE_HEX.ARCHITECTURE,
  5:  TYPE_HEX.MODULE,
  6:  TYPE_HEX.CONTRACT,
  7:  TYPE_HEX.LLR,
  8:  TYPE_HEX.CLASS,
  9:  TYPE_HEX.SUITE,
  10: TYPE_HEX.CASE,
  11: '#6366f1',  // indigo-500 — dashboard
  12: TYPE_HEX.TEST,
  13: TYPE_HEX.RESULT,
  14: TYPE_HEX.RECORD,
  15: '#fcd34d',  // amber-300 — certification / DO-178C gold
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Returns the effective type key for a node.
 * REQ nodes (legacy) are split into REQ_HLR / REQ_LLR based on `properties.req_level`.
 */
export function resolveTypeKey(n: NodeLike): string {
  if (n.node_type === 'REQ') {
    const level = n.properties.req_level as string | undefined;
    if (level === 'hlr') return 'REQ_HLR';
    if (level === 'llr') return 'REQ_LLR';
  }
  return n.node_type;
}

/** Tailwind class string for the node (text + bg + border). */
export function nodeTypeClass(n: NodeLike): string {
  return TYPE_COLOR[resolveTypeKey(n)] ?? 'text-slate-400 bg-slate-400/15 border-slate-400/30';
}

/** First token of the class string (text color only — for inline text spans). */
export function nodeTextClass(n: NodeLike): string {
  return nodeTypeClass(n).split(' ')[0];
}

/** Hex color for the node (used in canvas / 3D rendering). */
export function nodeHexColor(n: NodeLike): string {
  return TYPE_HEX[resolveTypeKey(n)] ?? '#94a3b8';
}

/** Text-color class for a bare node_type string (no req_level split). */
export function typeTextClass(nodeType: string): string {
  return (TYPE_COLOR[nodeType] ?? 'text-slate-400 bg-slate-400/15 border-slate-400/30').split(' ')[0];
}
