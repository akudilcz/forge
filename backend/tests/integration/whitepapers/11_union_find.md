# Disjoint-Set Union with Path Compression, Union by Rank, and Connectivity Queries

Python Library Specification

## Abstract

This document specifies a Python library implementing the disjoint-set (union-find)
data structure over the integer element domain `0 .. n-1`. It maintains a partition
of the elements under two operations — merge two components, and ask whether two
elements lie in the same component — in amortised near-constant time. Both
optimisations that make that bound achievable are mandatory and observable through
the public API: **path compression** in `find` and **union by rank** in `union`.
The library also reports component counts, component membership, and the
instrumentation needed to verify that the forest is actually being flattened
rather than merely answering correctly.

## 1. Overview and Design Rationale

A disjoint-set forest represents each component as a rooted tree; the root is its
canonical representative. `find(x)` walks parent pointers to the root; `union(a, b)`
links one root under the other. Implemented naively this degrades to O(n) per
operation, because an adversarial union sequence builds a path of length n. Two
independent fixes are required:

- **Union by rank** bounds tree height at O(log n) by linking the shallower root
  under the deeper one.
- **Path compression** re-points every node visited by a `find` directly at the
  root, so the cost of a deep walk is paid once and never again.

Either alone gives O(log n) amortised; together they give O(α(n)), where α is the
inverse Ackermann function and α(n) ≤ 4 for every n expressible on real hardware. A
structure that answers connectivity correctly but never compresses is therefore
*wrong under this specification even though every query returns the right answer*,
and §5 exists so that the difference is mechanically observable.

## 2. Representation

Three parallel arrays indexed by element, all of length n:

| Array | Initial value | Meaning |
|---|---|---|
| `parent[x]` | `x` | Parent pointer; a root satisfies `parent[x] == x` |
| `rank[x]` | `0` | Upper bound on the height of the subtree rooted at `x` |
| `size[x]` | `1` | Elements in the component, **meaningful at roots only** |

`rank` stops being the exact height once compression shortens paths but stays a
valid upper bound, which is all the union rule needs. `size` must be tracked
separately: union by rank does not link the smaller tree under the larger one, so
`rank` carries no cardinality information. A counter `component_count` starts at n
and is decremented on every successful merge; it must never be recomputed by
scanning.

## 3. Find with Path Compression

```
find(x):
    validate(x)
    root = x; hops = 0
    while parent[root] != root:      # ascent
        root = parent[root]; hops += 1
    pointer_hops += hops
    while parent[x] != root:         # compression pass
        nxt = parent[x]; parent[x] = root; x = nxt
    return root
```

The compression pass is **full**: every node on the ascent path points directly at
the root when `find` returns, not merely at its grandparent. `find` must be
iterative; a recursive formulation risks `RecursionError` and is forbidden by §10.

## 4. Union by Rank

```
union(a, b):
    ra, rb = find(a), find(b)
    if ra == rb: return False
    if rank[ra] < rank[rb]: swap(ra, rb)
    elif rank[ra] == rank[rb]: rank[ra] += 1
    parent[rb] = ra; size[ra] += size[rb]; component_count -= 1
    return True
```

The tie-break is normative, not a matter of taste: **when the two roots have equal
rank, the root of `b`'s tree becomes the child of the root of `a`'s tree**, and
`a`'s root has its rank incremented. Fixing the tie-break makes the forest a
deterministic function of the operation sequence (§7.8), which is what lets its
*shape* be asserted rather than only its answers. `rank[x]` is written only while
`x` is a root; path compression must **never** modify any rank.

## 5. Instrumentation

The optimisations are unobservable through `find` and `connected` alone, so these
observers are public contract, not debug aids. The first three are **pure**: they
never compress and never count.

- `parent_of(x)` — the immediate parent pointer. `rank_of(x)` — `rank[x]`.
- `path_length(x)` — parent links between `x` and its root as the forest currently
  stands; `0` for a root.
- `pointer_hops` — monotonically non-decreasing, `0` at construction. Every
  `find(x)` adds exactly the length of the path from `x` to its root **at the
  moment of the call**. `union` and `connected` add the hops of the two `find`
  calls they perform. Nothing else adds to it. `components()` is therefore not
  pure: it calls `find` on every element, so it compresses and it counts.

## 6. Complexity

| Operation | Amortised | Worst case (single call) | Space |
|---|---|---|---|
| `find`, `union`, `connected`, `component_size` | O(α(n)) | O(log n) | O(1) |
| `components` | O(n·α(n)) | O(n log n) | O(n) |
| `add` / construction | O(1) / O(n) | O(1) / O(n) | O(1) / O(n) |

Normative budget: after m operations on n elements, `pointer_hops` must never
exceed `2 · (n + m)`. Path compression satisfies this with room to spare. Omitting
it blows the budget on the canonical adversarial workload — union the elements
pairwise into a balanced binomial tree of height log2(n), then `find` every element
twice — where an uncompressed forest pays Θ(n log n) hops on the second sweep and a
compressed one pays at most n.

## 7. Correctness Properties

1. **Partition** — `components()` returns disjoint non-empty lists whose union is
   exactly `{0 .. n-1}`. Each inner list ascends; the outer list is ordered by each
   component's smallest member.
2. **Equivalence** — `connected` is reflexive, symmetric and transitive, and
   `connected(a, b)` is true exactly when `find(a) == find(b)`.
3. **Compression is real** — immediately after `find(x)`, every node that was on
   the ascent path satisfies `path_length(node) <= 1`, and a second consecutive
   `find(x)` costs at most one pointer hop.
4. **Amortised budget** — the `pointer_hops` bound of §6 holds for every sequence.
5. **Bounded height** — a root of rank r holds at least 2^r elements, so at all
   times `rank_of(x) <= floor(log2(n))` and `path_length(x) <= floor(log2(n))`.
6. **Rank monotonicity** — for every non-root x, `rank_of(parent_of(x)) >
   rank_of(x)`. Compression preserves this because it moves x to a strict ancestor,
   whose rank is strictly larger.
7. **Size consistency** — `component_size(x)` equals the length of x's component in
   `components()`, and the sizes sum to n.
8. **Determinism** — two structures driven through the same operation sequence have
   identical `parent_of`, `rank_of` and `pointer_hops` for every element; no
   iteration over a `set` or `dict` may influence the result.
9. **Idempotent union** — `union(a, b)` on already-connected elements returns
   `False` and leaves every rank and `component_count` unchanged; its only
   permitted side effect is the compression its two `find` calls perform, so on an
   already-compressed pair the parent array is unchanged too. Symmetrically, no
   number of `find` calls changes the partition: compression rewires the forest
   without moving an element between components.

## 8. Failure Modes and Edge Cases

- `n == 0` is legal: `component_count` is 0 and `components()` is `[]`. `n == 1`
  gives one component with `find(0) == 0` and `path_length(0) == 0`.
- `n < 0` raises `ValueError`. A non-integer count (`float`, `str`) raises
  `TypeError`; `bool` is rejected too, despite subclassing `int` — `UnionFind(True)`
  is a typing accident, not a request for a one-element structure.
- An element outside `0 .. n-1` raises `IndexError`. Negative indices must **not**
  be interpreted Python-style from the end. A non-integer element, `bool` included,
  raises `TypeError`. An edge that is not a two-element sequence raises `ValueError`.
- Self-loop `union(a, a)` is legal, returns `False`, changes nothing; repeated
  identical edges are legal, and only the first merges. `add()` appends element n as
  its own singleton and returns n, leaving existing parents, ranks and sizes alone.
- Deep forests must not raise `RecursionError`; §7.5 bounds height by log2(n) and
  `find` is iterative regardless.

## 9. Public API

```python
class UnionFind:
    def __init__(self, n: int) -> None: ...
    def __len__(self) -> int: ...
    def find(self, x: int) -> int: ...
    def union(self, a: int, b: int) -> bool: ...
    def connected(self, a: int, b: int) -> bool: ...
    def add(self) -> int: ...
    def component_size(self, x: int) -> int: ...
    def components(self) -> list[list[int]]: ...
    def parent_of(self, x: int) -> int: ...
    def rank_of(self, x: int) -> int: ...
    def path_length(self, x: int) -> int: ...
    @property
    def component_count(self) -> int: ...
    @property
    def pointer_hops(self) -> int: ...


def from_edges(n: int, edges: Iterable[tuple[int, int]]) -> UnionFind:
    """Build a structure of n elements and union each edge, in the given order."""

def connected_components(n: int, edges: Iterable[tuple[int, int]]) -> list[list[int]]:
    """Return the components of the graph (n, edges), ordered as in §7.1."""
```

## 10. Implementation Notes

- Do **not** delegate to any graph library. `networkx`, `scipy.sparse.csgraph`,
  `igraph`, `rustworkx`/`retworkx`, `graph_tool`, `networkit`, `pandas`, `numpy` and
  the stdlib `graphlib` are all forbidden: a wrapper around
  `networkx.connected_components` answers every connectivity query correctly while
  implementing none of the structure this specification is about. The three arrays
  of §2 and the two procedures of §3 and §4 are the deliverable.
- `find` is iterative; recursion is forbidden. `component_count` is a maintained
  counter, never a recomputed scan.
- `components()` groups by root in a single pass and sorts once; it must not call
  `find` more than once per element.
- Validation precedes mutation: a rejected element leaves the forest, the ranks and
  `pointer_hops` exactly as they were.
