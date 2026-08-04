/** Shared constants for the Agent Inspector dashboard. */

export const AGENT_COLOR: Record<string, string> = {
  'Document Specialist':   '#7dd3fc',  // sky-300  — phase 2 (PARA)
  'Requirements Engineer': '#fbbf24',  // amber-400 — phase 3 (HLR)
  'Design Architect':      '#fb7185',  // rose-400  — phase 4 (ARCHITECTURE)
  'Software Engineer':     '#818cf8',  // indigo-400 — phase 8 (CLASS)
  'Test Engineer':         '#2dd4bf',  // teal-400  — phase 9 (SUITE)
  'Quality Auditor':       '#94a3b8',  // slate-400 — phase 13
};

export interface AgentDefinition {
  role: string;
  model: string;
  goal: string;
  tools: string[];
  gap_types: string[];
}

export interface ApiAgentState {
  agent_id: string;
  display_name: string;
  status: string;
  current_task: string | null;
  tasks_completed: number;
  tokens_used: number;
}
