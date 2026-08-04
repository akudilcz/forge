/**
 * Shared types for Phase 12 dashboard components.
 */

import type { LineTrace, SuggestedTrace, UntracedFunction } from './CodeTraceView';

export interface TracedFile {
  nodeId: string;
  filePath: string;
  lineTraces: LineTrace[];
  suggestedTraces: SuggestedTrace[];
  untracedFunctions: UntracedFunction[];
  traceCoverage: { total: number; traced: number } | null;
}
