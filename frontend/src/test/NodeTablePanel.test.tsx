/**
 * Behavioural tests for NodeTablePanel.
 *
 * Mocks:
 *  - @tanstack/react-query (edge loading returns empty lists so tests are fast)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { NodeTablePanel, type GNode } from '@/components/NodeTablePanel';
import React from 'react';

// ── Mocks ──────────────────────────────────────────────────────────────────────

// Stub TanStack Query — NodeDetail uses useQuery only for edge loading.
// Returning empty edges keeps tests deterministic and fast.
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: { outgoing: [], incoming: [] },
    isLoading: false,
  }),
  QueryClient: vi.fn(),
  QueryClientProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// ── Fixtures ───────────────────────────────────────────────────────────────────

function makeNode(overrides: Partial<GNode> = {}): GNode {
  return {
    node_id: 'test.node.001',
    node_type: 'REQ',
    layer: 2,
    title: 'Test Requirement',
    lifecycle: 'active',
    version: 1,
    content: 'The system shall do something.',
    content_hash: 'abc123',
    parent_id: null,
    trace_to: [],
    properties: {},
    ...overrides,
  };
}

const DOC_NODE = makeNode({
  node_id: 'doc.001',
  node_type: 'DOCUMENT',
  title: 'Main Document',
  content: 'Document content here.',
});

const REQ_NODE = makeNode({
  node_id: 'req.001',
  node_type: 'REQ',
  title: 'REQ-001',
  content: 'System shall respond within 200ms.',
});

const PARA_NODE = makeNode({
  node_id: 'para.001',
  node_type: 'PARA',
  title: 'Paragraph 1',
  content: 'Some paragraph text.',
});

// ── Helpers ────────────────────────────────────────────────────────────────────

function renderPanel(
  nodes: GNode[] = [],
  extraDetail?: (n: GNode) => React.ReactNode,
) {
  return render(
    <NodeTablePanel
      nodes={nodes}
      isLoading={false}
      title="Test Panel"
      extraDetail={extraDetail}
    />,
  );
}

/**
 * Return the type chip button for a given type label.
 * Chip buttons have short accessible names: "TYPE COUNT" (e.g. "DOCUMENT 1").
 * Row buttons have long names that also start with the type label but include
 * title, content preview, and node_id — so we match the end with \d+$.
 */
function getTypeChip(label: string): HTMLElement {
  // Matches "ALL 3", "DOCUMENT 1", "REQ 2", etc. but NOT row buttons
  const buttons = screen.getAllByRole('button', { name: new RegExp(`^${label}\\s+\\d+$`) });
  if (buttons.length !== 1) {
    throw new Error(`Expected exactly 1 "${label}" chip, found ${buttons.length}`);
  }
  return buttons[0];
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('NodeTablePanel — list rendering', () => {
  it('shows empty message when no nodes', () => {
    renderPanel([]);
    expect(screen.getByText('No nodes yet.')).toBeInTheDocument();
  });

  it('renders a row for each node', () => {
    renderPanel([REQ_NODE, DOC_NODE]);
    expect(screen.getByText('REQ-001')).toBeInTheDocument();
    expect(screen.getByText('Main Document')).toBeInTheDocument();
  });

  it('shows total node count in the header', () => {
    renderPanel([REQ_NODE, DOC_NODE, PARA_NODE]);
    // The header count span shows the filtered count; when no filter active it shows total
    const counts = screen.getAllByText('3');
    expect(counts.length).toBeGreaterThan(0);
  });
});

describe('NodeTablePanel — TypeChips filter', () => {
  it('renders type chips when multiple types present', () => {
    renderPanel([REQ_NODE, DOC_NODE]);
    // Use role=button + name matching to target chips specifically (not type badges in rows)
    expect(getTypeChip('ALL')).toBeInTheDocument();
    expect(getTypeChip('DOCUMENT')).toBeInTheDocument();
    expect(getTypeChip('REQ')).toBeInTheDocument();
  });

  it('does not render type chips when only one type present', () => {
    renderPanel([REQ_NODE, makeNode({ node_id: 'req.002', node_type: 'REQ', title: 'REQ-002' })]);
    expect(screen.queryByRole('button', { name: /^ALL\s/ })).not.toBeInTheDocument();
  });

  it('filters rows when a type chip is clicked', () => {
    renderPanel([REQ_NODE, DOC_NODE, PARA_NODE]);
    fireEvent.click(getTypeChip('REQ'));

    expect(screen.getByText('REQ-001')).toBeInTheDocument();
    expect(screen.queryByText('Main Document')).not.toBeInTheDocument();
    expect(screen.queryByText('Paragraph 1')).not.toBeInTheDocument();
  });

  it('clicking ALL chip clears the type filter', () => {
    renderPanel([REQ_NODE, DOC_NODE]);

    fireEvent.click(getTypeChip('REQ'));
    expect(screen.queryByText('Main Document')).not.toBeInTheDocument();

    fireEvent.click(getTypeChip('ALL'));
    expect(screen.getByText('Main Document')).toBeInTheDocument();
  });

  it('clicking the active chip again clears the filter', () => {
    renderPanel([REQ_NODE, DOC_NODE]);
    fireEvent.click(getTypeChip('REQ'));
    expect(screen.queryByText('Main Document')).not.toBeInTheDocument();
    fireEvent.click(getTypeChip('REQ'));
    expect(screen.getByText('Main Document')).toBeInTheDocument();
  });
});

describe('NodeTablePanel — search', () => {
  it('filters nodes by title', () => {
    renderPanel([REQ_NODE, DOC_NODE]);
    const input = screen.getByPlaceholderText(/Search id, label, content/);
    fireEvent.change(input, { target: { value: 'REQ-001' } });
    expect(screen.getByText('REQ-001')).toBeInTheDocument();
    expect(screen.queryByText('Main Document')).not.toBeInTheDocument();
  });

  it('shows "No matches." when search finds nothing', () => {
    renderPanel([REQ_NODE]);
    const input = screen.getByPlaceholderText(/Search id, label, content/);
    fireEvent.change(input, { target: { value: 'zzznomatch' } });
    expect(screen.getByText('No matches.')).toBeInTheDocument();
  });

  it('filters by content text', () => {
    renderPanel([REQ_NODE, DOC_NODE]);
    const input = screen.getByPlaceholderText(/Search id, label, content/);
    fireEvent.change(input, { target: { value: '200ms' } });
    expect(screen.getByText('REQ-001')).toBeInTheDocument();
    expect(screen.queryByText('Main Document')).not.toBeInTheDocument();
  });
});

describe('NodeTablePanel — NodeDetail (no tabs)', () => {
  beforeEach(() => {
    renderPanel([REQ_NODE]);
    fireEvent.click(screen.getByText('REQ-001'));
  });

  it('shows the Content section in the detail panel', () => {
    // The "Content" section heading appears only after the detail panel opens
    const headings = screen.getAllByText('Content');
    expect(headings.length).toBeGreaterThan(0);
  });

  it('shows the node content inside the detail panel', () => {
    // Content is shown in both the row preview and the detail panel
    const matches = screen.getAllByText('System shall respond within 200ms.');
    expect(matches.length).toBeGreaterThan(0);
  });

  it('does NOT have tab buttons (content/properties/edges as tabs)', () => {
    // Tabs are replaced by stacked sections — no tab-role buttons for these labels
    expect(screen.queryByRole('button', { name: /^content$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^properties$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^edges$/i })).not.toBeInTheDocument();
  });

  it('renders all sections simultaneously without switching', () => {
    // After opening the detail, Content heading is visible immediately
    expect(screen.getAllByText('Content').length).toBeGreaterThan(0);
    // No need to click a tab to see content
  });

  it('closes the detail panel when the close button is clicked', () => {
    // The close button is in the detail panel header — find it by its position
    // Look for all small icon-only buttons (close buttons have no text content)
    const allButtons = screen.getAllByRole('button');
    const closeBtn = allButtons.find(b => {
      const hasSvg = b.querySelector('svg') !== null;
      const hasNoText = !b.textContent?.trim();
      return hasSvg && hasNoText;
    });
    expect(closeBtn).toBeDefined();
    fireEvent.click(closeBtn!);
    // After closing, the detail panel sections are gone
    expect(screen.queryByText('Content')).not.toBeInTheDocument();
  });
});

describe('NodeTablePanel — NodeDetail with properties', () => {
  it('shows the Properties section when node has properties', () => {
    const nodeWithProps = makeNode({
      node_id: 'req.props',
      title: 'Props Node',
      content: 'Unique content xyzzy.',
      properties: { req_level: 'llr', priority: 'high' },
    });
    renderPanel([nodeWithProps]);
    fireEvent.click(screen.getByText('Props Node'));

    // Property keys and values rendered in the table
    // String values render without JSON quotes: String('llr') → 'llr'
    expect(screen.getByText('req_level')).toBeInTheDocument();
    expect(screen.getByText('llr')).toBeInTheDocument();
    expect(screen.getByText('priority')).toBeInTheDocument();
    expect(screen.getByText('high')).toBeInTheDocument();
  });

  it('hides Properties section when node has no properties', () => {
    renderPanel([REQ_NODE]); // REQ_NODE has empty properties: {}
    fireEvent.click(screen.getByText('REQ-001'));
    expect(screen.queryByText(/^Properties/)).not.toBeInTheDocument();
  });
});

describe('NodeTablePanel — extraDetail render prop', () => {
  it('renders extraDetail content below the standard sections', () => {
    renderPanel(
      [REQ_NODE],
      (node) => (
        <section data-testid="extra-section">
          <span>Custom section for {node.node_id}</span>
        </section>
      ),
    );
    fireEvent.click(screen.getByText('REQ-001'));
    expect(screen.getByTestId('extra-section')).toBeInTheDocument();
    expect(screen.getByText('Custom section for req.001')).toBeInTheDocument();
  });

  it('does not render extraDetail when no node is selected', () => {
    renderPanel(
      [REQ_NODE],
      () => <section data-testid="extra-section">Extra</section>,
    );
    expect(screen.queryByTestId('extra-section')).not.toBeInTheDocument();
  });

  it('does not require extraDetail to be provided', () => {
    expect(() => renderPanel([REQ_NODE])).not.toThrow();
  });
});
