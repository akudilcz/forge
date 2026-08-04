/**
 * Trace status helpers for Phase 12 dashboard.
 *
 * Simple model:
 * - RESULT nodes have file_path + function_name + status
 * - Match them directly to test functions in the source tree
 * - No graph chain needed — RESULT is the source of truth
 */

import type { GNode } from '@/components/NodeTablePanel';
import type { TracedFile } from './types';

// ── Types ──────────────────────────────────────────────────────────────────

export interface FunctionEntry {
  name: string;
  className: string;
  startLine: number;
  endLine: number;
  isTraced: boolean;
  llrIds: string[];
  caseIds: string[];
}

export type ResultStatus = 'passed' | 'failed' | 'skipped' | 'unknown';

// ── Result map ─────────────────────────────────────────────────────────────

/**
 * Build a map from function_name → ResultStatus from RESULT nodes.
 *
 * RESULT nodes carry file_path and function_name in their properties.
 * This gives a direct lookup: "did this test function pass?"
 * No CASE chain indirection needed.
 */
export function buildResultMap(nodes: GNode[]): Map<string, ResultStatus> {
  const results = new Map<string, ResultStatus>();
  for (const n of nodes) {
    if (n.node_type !== 'RESULT') continue;
    const status = resolveResultStatus(n);
    const funcName = n.properties?.function_name as string | undefined;
    if (funcName) {
      _mergeStatus(results, funcName, status);
    }
    // Also key by test_id for broader matching
    const testId = n.properties?.test_id as string | undefined;
    if (testId) {
      _mergeStatus(results, testId, status);
    }
    // Also key by CASE node IDs for @traces(case="CASE_LLR-X") style
    for (const targetId of n.trace_to) {
      _mergeStatus(results, targetId, status);
    }
  }
  return results;
}

// Backward compat — old callers use buildCaseResultMap
export const buildCaseResultMap = buildResultMap;

function _mergeStatus(
  map: Map<string, ResultStatus>, key: string, status: ResultStatus,
): void {
  const existing = map.get(key);
  if (!existing || status === 'failed') {
    map.set(key, status);
  }
}

function resolveResultStatus(node: GNode): ResultStatus {
  const propStatus = node.properties?.status as string | undefined;
  if (propStatus === 'passed' || propStatus === 'failed' || propStatus === 'skipped') {
    return propStatus;
  }
  if (propStatus === 'pass') return 'passed';
  if (propStatus === 'fail') return 'failed';
  return 'unknown';
}

// ── Function status ────────────────────────────────────────────────────────

/** Determine function dot colour for a function in a test file. */
export function testFuncStatus(
  func: FunctionEntry,
  resultMap: Map<string, ResultStatus>,
): 'green' | 'amber' | 'red' {
  // Helper functions (not starting with test_) are judged purely on
  // whether they have @traces — they don't appear in RESULT nodes.
  const isTestFunc = func.name.startsWith('test_') || func.name.startsWith('test');
  if (!isTestFunc) {
    return func.isTraced ? 'green' : 'red';
  }

  const hasTrace = func.isTraced;

  // Direct lookup by function name
  const byName = resultMap.get(func.name);

  // Also check by CASE IDs (for @traces(case="CASE_LLR-X") style)
  const byCaseId = func.caseIds.map(id => resultMap.get(id)).filter(Boolean);

  const hasPassing = byName === 'passed' || byCaseId.some(s => s === 'passed');
  const hasFailing = byName === 'failed' || byCaseId.some(s => s === 'failed');

  if (hasFailing) return 'red';
  if (hasTrace && hasPassing) return 'green';
  if (hasTrace || hasPassing) return 'amber';
  return 'red';
}

// ── File-level status (worst-case of children) ────────────────────────────

/** Compute worst-case status for a file from its child function statuses. */
export function fileLevelStatus(
  funcs: FunctionEntry[],
  isTestFile: boolean,
  resultMap: Map<string, ResultStatus>,
): 'green' | 'amber' | 'red' | 'grey' {
  if (funcs.length === 0) return 'grey';

  const statuses = funcs.map(f =>
    isTestFile ? testFuncStatus(f, resultMap) : (f.isTraced ? 'green' : 'red'),
  );

  if (statuses.some(s => s === 'red')) return 'red';
  if (statuses.some(s => s === 'amber')) return 'amber';
  return 'green';
}

// ── Function extraction ────────────────────────────────────────────────────

/** Extract functions from a TracedFile's lineTraces and untracedFunctions. */
export function extractFunctions(traced: TracedFile): FunctionEntry[] {
  const funcs: FunctionEntry[] = [];
  const seen = new Set<string>();

  const qkey = (cls: string | undefined, name: string) =>
    cls ? `${cls}.${name}` : name;

  for (const t of traced.lineTraces) {
    const key = qkey(t.class_name, t.symbol);
    if (t.symbol && !seen.has(key)) {
      seen.add(key);
      funcs.push({
        name: t.symbol,
        className: t.class_name ?? '',
        startLine: t.start,
        endLine: t.end,
        isTraced: true,
        llrIds: [...t.llr_ids],
        caseIds: [...(t.case_ids ?? [])],
      });
    }
  }

  // Merge duplicate trace entries (same function, multiple @traces calls)
  for (const t of traced.lineTraces) {
    if (!t.symbol) continue;
    const key = qkey(t.class_name, t.symbol);
    const existing = funcs.find(f => qkey(f.className, f.name) === key);
    if (existing) {
      for (const id of t.llr_ids) {
        if (!existing.llrIds.includes(id)) existing.llrIds.push(id);
      }
      for (const id of (t.case_ids ?? [])) {
        if (!existing.caseIds.includes(id)) existing.caseIds.push(id);
      }
    }
  }

  for (const u of traced.untracedFunctions) {
    const key = qkey(u.class_name, u.name);
    if (!seen.has(key)) {
      seen.add(key);
      funcs.push({
        name: u.name,
        className: u.class_name ?? '',
        startLine: u.start,
        endLine: u.end,
        isTraced: false,
        llrIds: [],
        caseIds: [],
      });
    }
  }

  funcs.sort((a, b) => a.startLine - b.startLine);
  return funcs;
}
