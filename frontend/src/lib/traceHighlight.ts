/**
 * traceHighlight — compute related node IDs for selection highlighting.
 *
 * When a node is selected, returns the set of node IDs that should be
 * visually highlighted: structural parent/children, outgoing trace_to
 * targets, and incoming reverse-trace sources.
 */

import type { GNode } from '@/components/NodeTablePanel';

/** Build the set of node IDs related to the selected node. */
export function buildRelatedNodeIds(
  selectedNodeId: string | null,
  nodes: GNode[],
): Set<string> {
  if (!selectedNodeId) return new Set();

  const selected = nodes.find(n => n.node_id === selectedNodeId);
  if (!selected) return new Set();

  const related = new Set<string>();

  // Structural: parent
  if (selected.parent_id) related.add(selected.parent_id);

  // Structural: direct children
  for (const n of nodes) {
    if (n.parent_id === selectedNodeId) related.add(n.node_id);
  }

  // Outgoing trace_to targets
  for (const tid of selected.trace_to ?? []) {
    related.add(tid);
  }

  // Incoming: nodes whose trace_to includes this node
  for (const n of nodes) {
    if ((n.trace_to ?? []).includes(selectedNodeId)) {
      related.add(n.node_id);
    }
  }

  // Don't include the selected node itself — it has its own style
  related.delete(selectedNodeId);

  return related;
}
