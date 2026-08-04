# Topological Ordering with Kahn's Algorithm and Cycle Diagnosis

Python Library Specification

## Abstract

This document specifies a Python library that computes a topological ordering of
a directed graph using Kahn's algorithm, and, when no such ordering exists,
reports a concrete cycle witnessing the failure. The library additionally
computes the set of strongly connected components via Tarjan's algorithm, and
supports deterministic tie-breaking so that the ordering is reproducible across
runs.

## 1. Overview and Design Rationale

A topological sort answers "in what order may these tasks run?" Kahn's algorithm
is chosen over DFS-based ordering because it produces the answer incrementally
from in-degree counts, which makes two things natural: deterministic
tie-breaking via a priority queue, and cycle detection as a simple count check at
the end.

Reporting *that* a graph is cyclic is rarely enough — the caller needs to know
*which* nodes form the cycle. Kahn's algorithm leaves exactly the nodes on cycles
(and nodes reachable from them) unprocessed, so a second pass over the residual
graph recovers a concrete cycle path.

## 2. Graph Representation

A graph is constructed from an adjacency mapping from node to an iterable of its
successors. Nodes must be hashable. An edge `u -> v` means u must precede v.

- Nodes appearing only as successors are implicitly added to the node set.
- The graph is allowed to be disconnected.
- Duplicate edges are collapsed; `{"a": ["b", "b"]}` has one edge and in-degree
  of `b` is 1, not 2. This is a required test: naive in-degree counting produces
  a node that is never emitted.
- A self-loop `u -> u` is a cycle of length 1 and makes the graph non-orderable.

## 3. Kahn's Algorithm

```
topological_sort(graph, tie_breaker=None):
    in_degree = {n: 0 for n in nodes}
    for u in nodes:
        for v in unique_successors(u):
            in_degree[v] += 1

    ready = all nodes with in_degree 0
    order = []
    while ready:
        u = take_next(ready, tie_breaker)
        order.append(u)
        for v in unique_successors(u):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                ready.add(v)

    if len(order) != len(nodes):
        raise CyclicGraphError(cycle=find_cycle(graph, unprocessed))
    return order
```

### 3.1 Tie-Breaking and Determinism

When several nodes have in-degree zero simultaneously, the choice is ambiguous.
The library resolves it as follows:

- Default: nodes are emitted in ascending order of a `sort_key`, defaulting to
  the node itself. This requires nodes be mutually comparable; if they are not,
  insertion order is used instead.
- A caller-supplied `tie_breaker` callable overrides this.

The consequence is that `topological_sort` is a pure function: the same graph
yields the same list on every run and every platform. Iterating a Python `set` to
choose the next node would violate this and is explicitly forbidden.

## 4. Cycle Recovery

When `len(order) < len(nodes)`, the residual nodes are those with non-zero
in-degree. To produce a witness cycle:

1. Restrict the graph to residual nodes and edges between them.
2. From any residual node, walk successors, recording the path and the position
   of each node in it.
3. On revisiting a node already on the current path, slice the path from that
   node's recorded position to the end — that slice is a cycle.

The returned cycle is a list of nodes in traversal order, where an edge exists
from each node to the next and from the last back to the first. The cycle must be
minimal in the sense that no node repeats within it except the implicit closure.

## 5. Strongly Connected Components

`strongly_connected_components(graph)` returns a list of components using
Tarjan's algorithm, each component a list of nodes. Ordering guarantees:

- Components are returned in reverse topological order of the condensation.
- Nodes within a component are sorted by the same tie-breaking rule as §3.1.
- Every node appears in exactly one component. A node with no cycles forms a
  singleton component.

Tarjan's algorithm must be implemented iteratively with an explicit stack; a
recursive implementation would fail the deep-graph case in §9.

## 6. Derived Queries

- `is_acyclic(graph) -> bool` — True iff a topological order exists. Must not
  raise on a cyclic graph.
- `longest_path_length(graph) -> int` — number of edges on the longest path in a
  DAG, computed by relaxing along the topological order. Raises
  `CyclicGraphError` if the graph is cyclic.
- `ancestors(graph, node)` / `descendants(graph, node)` — transitive closure sets,
  excluding the node itself unless it lies on a cycle.

## 7. Complexity

| Operation | Time | Space |
|---|---|---|
| topological_sort | O(V + E log V) with sorted tie-break | O(V + E) |
| topological_sort | O(V + E) with insertion-order tie-break | O(V + E) |
| find_cycle | O(V + E) | O(V) |
| strongly_connected_components | O(V + E) | O(V) |
| longest_path_length | O(V + E) | O(V) |

## 8. Correctness Properties

1. **Ordering validity** — for every edge `u -> v` in a DAG, `index(u) < index(v)`
   in the returned order.
2. **Completeness** — the returned order contains every node exactly once.
3. **Determinism** — repeated calls on an equal graph return an identical list.
4. **Cycle soundness** — every node pair adjacent in a reported cycle has a real
   edge between them, and the last node has an edge to the first.
5. **Cycle completeness** — if the graph is cyclic, a cycle is always reported;
   the library never claims a cyclic graph is acyclic.
6. **SCC partition** — components partition the node set; their union is all
   nodes and they are pairwise disjoint.

## 9. Failure Modes and Edge Cases

- Empty graph: returns an empty list, not an error.
- Single node, no edges: returns that node.
- Self-loop: cyclic, reported cycle is `[u]`.
- Two-node cycle `a -> b -> a`: reported cycle has length 2.
- Duplicate edges: must not corrupt in-degree (see §2).
- Disconnected components: all are ordered; relative order between components is
  determined by tie-breaking.
- Edge referencing a node absent from the mapping keys: that node is added
  implicitly with no outgoing edges.
- A 10,000-node path graph must not raise `RecursionError` — both the ordering
  and the SCC pass must be iterative.
- Nodes that are not mutually comparable (e.g. mixed `int` and `str`) must fall
  back to insertion order rather than raising `TypeError`.

## 10. Public API

```python
class CyclicGraphError(ValueError):
    cycle: list[Any]
    def __str__(self) -> str:
        """e.g. "graph contains a cycle: a -> b -> c -> a" """

def topological_sort(
    graph: Mapping[Any, Iterable[Any]],
    *,
    tie_breaker: Callable[[Any], Any] | None = None,
) -> list[Any]: ...

def is_acyclic(graph: Mapping[Any, Iterable[Any]]) -> bool: ...

def find_cycle(graph: Mapping[Any, Iterable[Any]]) -> list[Any] | None: ...

def strongly_connected_components(
    graph: Mapping[Any, Iterable[Any]],
) -> list[list[Any]]: ...

def longest_path_length(graph: Mapping[Any, Iterable[Any]]) -> int: ...

def ancestors(graph: Mapping[Any, Iterable[Any]], node: Any) -> set[Any]: ...

def descendants(graph: Mapping[Any, Iterable[Any]], node: Any) -> set[Any]: ...
```

## 11. Implementation Notes

- Do not use `graphlib.TopologicalSorter` or `networkx`; the explicit algorithms
  are the subject of the specification.
- In-degree computation must deduplicate successors before counting.
- Both Kahn's algorithm and Tarjan's must be iterative.
