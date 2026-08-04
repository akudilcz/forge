import {
  Box, FileText, Layers, Scissors, List,
  Blocks, FileCode2, AlignLeft, Code2,
  FlaskConical, CheckSquare, FileCode, PlayCircle,
  LayoutDashboard, Package,
  type LucideIcon,
} from 'lucide-react';

export type PhaseConfig = {
  name: string;
  icon: LucideIcon;
  nodeTypes: string[];   // node_type values shown in NodeTablePanel
  gapTypes: string[];    // gap types belonging to this phase
  description: string;
  qualCheck?: boolean;   // true → show "Qual Check" button in PhaseDashboard
};

// Canonical 14-phase mapping (phases 0-13) — mirrors docs/06 Gap Analyser.md §Part Four.
export const PHASE_CONFIG: Record<number, PhaseConfig> = {
  0: {
    name: 'Initialise Project',
    icon: Box,
    nodeTypes: ['PROJECT'],
    gapTypes: [],
    description: 'Workspace initialisation — creates a fresh node-graph database at [WORKSPACE]/.forge and the PROJECT root node (human-initiated).',
  },
  1: {
    name: 'Read Forge.md',
    icon: FileText,
    nodeTypes: ['DOCUMENT'],
    gapTypes: [],
    description: 'Read Forge.md — the DOCUMENT node is created from the Forge.md file in the workspace root (human-initiated).',
  },
  2: {
    name: 'Chunk Forge.md',
    icon: Scissors,
    nodeTypes: ['PARA'],
    gapTypes: ['UNCHUNKED_DOCUMENT'],
    description: 'Document Specialist splits the Forge.md DOCUMENT into PARA nodes via semantic chunking.',
    qualCheck: true,
  },
  3: {
    name: 'Requirements (HLR)',
    icon: List,
    nodeTypes: ['HLR'],
    gapTypes: ['UNCOVERED_PARA'],
    description: 'Requirements Engineer derives a High-Level Requirement from each body paragraph.',
    qualCheck: true,
  },
  4: {
    name: 'Architecture',
    icon: Layers,
    nodeTypes: ['ARCHITECTURE'],
    gapTypes: ['UNARCHITECTED'],
    description: 'Design Architect produces the ARCHITECTURE ideation document tracing to all HLRs.',
    qualCheck: true,
  },
  5: {
    name: 'Modularisation',
    icon: Blocks,
    nodeTypes: ['MODULE'],
    gapTypes: ['UNMODULARISED'],
    description: 'Design Architect creates MODULE nodes under the ARCHITECTURE, each tracing to its HLRs.',
    qualCheck: true,
  },
  6: {
    name: 'Contracts (API)',
    icon: FileCode2,
    nodeTypes: ['CONTRACT'],
    gapTypes: ['UNCONTRACTED'],
    description: 'Design Architect defines one CONTRACT per MODULE — public interface, pre/post-conditions.',
    qualCheck: true,
  },
  7: {
    name: 'Requirements (LLR)',
    icon: AlignLeft,
    nodeTypes: ['LLR'],
    gapTypes: ['UNREFINED_HLR'],
    description: 'Requirements Engineer elaborates each HLR into one or more LLRs sized to module scope.',
    qualCheck: true,
  },
  8: {
    name: 'Design',
    icon: Code2,
    nodeTypes: ['DESIGN'],
    gapTypes: ['UNDESIGNED'],
    description: 'Software Engineer creates DESIGN specs (class API, method signatures, responsibilities — no code).',
    qualCheck: true,
  },
  9: {
    name: 'Test Suite',
    icon: FlaskConical,
    nodeTypes: ['SUITE'],
    gapTypes: ['UNSUITED'],
    description: 'Test Engineer creates the SUITE test-strategy document under the PROJECT.',
    qualCheck: true,
  },
  10: {
    name: 'Verification',
    icon: CheckSquare,
    nodeTypes: ['CASE_HLR', 'CASE_LLR'],
    gapTypes: ['UNTESTED_HLR', 'UNTESTED_LLR'],
    description: 'Test Engineer writes CASE_HLR nodes for every HLR (system tests) and CASE_LLR nodes for every LLR (unit tests).',
    qualCheck: true,
  },
  11: {
    name: 'Doco Render',
    icon: LayoutDashboard,
    nodeTypes: [],
    gapTypes: [],
    description: 'Deterministic render of phase 3–10 documentation into [workspace]/docs/ as Markdown files.',
  },
  12: {
    name: 'Code Gen',
    icon: FileCode,
    nodeTypes: ['DESIGN', 'CASE_HLR', 'CASE_LLR'],
    gapTypes: [],
    description: 'Calls claude CLI to generate source code and pytest tests from DESIGN and CASE specifications. Produces line-level LLR traceability.',
  },
  13: {
    name: 'Workspace Sync',
    icon: PlayCircle,
    nodeTypes: ['CODE', 'TEST'],
    gapTypes: ['UNSYNCED_DESIGN', 'UNSYNCED_TEST'],
    description: 'Reconciles generated workspace files against DESIGN and CASE specs. Creates CODE and TEST graph nodes.',
    qualCheck: true,
  },
  14: {
    name: 'Deliverables',
    icon: Package,
    nodeTypes: [],
    gapTypes: [],
    description: 'Builds a professional deliverables pack — requirements, architecture, design, test plan, traceability matrix, coverage report — bundled with source code and tests as a downloadable ZIP.',
  },
};

export const NUM_PHASES = 15;

/** Quality gap types — surfaced by qual-check runs on any phase. */
export const QUALITY_GAP_TYPES = [
  'STALE_NODE', 'ORPHAN_NODE', 'EMPTY_CONTENT', 'STALE_TRACE_TO',
  'INCONSISTENT_CONTENT', 'MALFORMED_REQUIREMENT', 'UNTITLED_NODE',
  'DUPLICATE_NODE',
] as const;

/** Human-readable agent role for each gap type — mirrors GAP_AGENT_MAPPING on the backend. */
export const GAP_AGENT_ROLE: Record<string, string> = {
  UNCHUNKED_DOCUMENT: 'Document Specialist',
  UNCOVERED_PARA:     'Requirements Engineer',
  UNARCHITECTED:      'Design Architect',
  UNMODULARISED:      'Design Architect',
  UNCONTRACTED:       'Design Architect',
  UNREFINED_HLR:      'Requirements Engineer',
  UNDESIGNED:         'Software Engineer',
  UNSUITED:           'Test Engineer',
  UNTESTED_HLR:       'Test Engineer',
  UNTESTED_LLR:       'Test Engineer',
  UNSYNCED_DESIGN:    'Software Engineer',
  UNSYNCED_TEST:      'Test Engineer',
  STALE_NODE:             'Quality Auditor',
  ORPHAN_NODE:            'Quality Auditor',
  EMPTY_CONTENT:          'Quality Auditor',
  STALE_TRACE_TO:         'Quality Auditor',
  INCONSISTENT_CONTENT:   'Quality Auditor',
  MALFORMED_REQUIREMENT:  'Quality Auditor',
  UNTITLED_NODE:          'Quality Auditor',
};

/** Map each gap type to its phase number — structural gaps only (quality gaps route per node). */
export const GAP_PHASE: Record<string, number> = {
  UNCHUNKED_DOCUMENT: 2,
  UNCOVERED_PARA:     3,
  UNARCHITECTED:      4,
  UNMODULARISED:      5,
  UNCONTRACTED:       6,
  UNREFINED_HLR:      7,
  UNDESIGNED:         8,
  UNSUITED:           9,
  UNTESTED_HLR:       10,
  UNTESTED_LLR:       10,
  UNSYNCED_DESIGN:    13,
  UNSYNCED_TEST:      13,
};
