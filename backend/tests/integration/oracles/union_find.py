"""Oracle for whitepapers/11_union_find.md.

Authored from the whitepaper only; never shown to any agent.

This is the whitepaper where a *correct* implementation and a *conformant* one are
hardest to tell apart from the outside. A disjoint-set forest with no path
compression and no union by rank answers every connectivity query correctly — it
is merely O(n) per operation instead of O(α(n)). So the centre of gravity of this
oracle is §5's instrumentation and §7.3–§7.6: the properties that make compression
and ranking *observable*. The connectivity answers are checked too, but the naive
forest passes those, so they earn little.

Section references below point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

import random
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition

# ── shared workload builders ─────────────────────────────────────────────────


def _binomial(cls: Any, k: int) -> Any:
    """Union roots pairwise into a balanced binomial tree of height k (§6).

    Every union here joins two *roots* of equal rank, so no `find` performed
    during the build walks a single pointer and the tree survives at its full
    height. That is the only way to construct a deep forest once compression is
    working, and it is what makes the depth assertions below meaningful.
    """
    n = 1 << k
    uf = cls(n)
    step = 1
    while step < n:
        for i in range(0, n, 2 * step):
            uf.union(i, i + step)
        step *= 2
    return uf


def _churn(cls: Any, n: int, ops: int, seed: int) -> Any:
    """A deterministic interleaving of unions, finds and connectivity queries."""
    rng = random.Random(seed)
    uf = cls(n)
    for _ in range(ops):
        a, b = rng.randrange(n), rng.randrange(n)
        roll = rng.random()
        if roll < 0.35:
            uf.union(a, b)
        elif roll < 0.7:
            uf.find(a)
        else:
            uf.connected(a, b)
    return uf


def _forest(uf: Any) -> tuple[list[int], list[int]]:
    """§5 — the full parent and rank arrays, read through the pure observers."""
    n = len(uf)
    return [uf.parent_of(i) for i in range(n)], [uf.rank_of(i) for i in range(n)]


def _bfs_components(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """An independent partition, computed by breadth-first search, not union-find.

    Deliberately a different algorithm: it shares no code path and no bug with the
    structure under test.
    """
    adjacency: dict[int, list[int]] = {i: [] for i in range(n)}
    for a, b in edges:
        adjacency[a].append(b)
        adjacency[b].append(a)
    seen: set[int] = set()
    out: list[list[int]] = []
    for start in range(n):
        if start in seen:
            continue
        seen.add(start)
        queue = [start]
        group: list[int] = []
        while queue:
            node = queue.pop()
            group.append(node)
            for nxt in adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        out.append(sorted(group))
    return sorted(out, key=lambda g: g[0])


# ── §7 correctness properties ────────────────────────────────────────────────


def _fresh_structure_is_all_singletons(cls: Any) -> bool:
    """§2 — parent[x] == x, rank 0, size 1, and no hops spent yet."""
    uf = cls(5)
    if uf.component_count != 5 or len(uf) != 5 or uf.pointer_hops != 0:
        return False
    for x in range(5):
        if uf.find(x) != x or uf.parent_of(x) != x:
            return False
        if uf.rank_of(x) != 0 or uf.path_length(x) != 0 or uf.component_size(x) != 1:
            return False
    return bool(uf.components() == [[0], [1], [2], [3], [4]] and uf.pointer_hops == 0)


def _worked_example_forest_shape(cls: Any) -> bool:
    """§4 — the mandated tie-break fixes the forest exactly, not just its answers.

    union(0,1) and union(2,3) each link equal-rank roots, so 1 goes under 0 and 3
    under 2, with rank[0] = rank[2] = 1. union(1,3) then links the two rank-1
    roots, putting 2 under 0 and raising rank[0] to 2. An implementation that
    breaks ties the other way, or that unions by size, produces a different array
    here even though every connectivity answer is identical.
    """
    uf = cls(4)
    if not (uf.union(0, 1) and uf.union(2, 3) and uf.union(1, 3)):
        return False
    if _forest(uf) != ([0, 0, 0, 2], [2, 0, 1, 0]):
        return False
    before = uf.pointer_hops
    root = uf.find(3)
    cost = uf.pointer_hops - before
    # 3 -> 2 -> 0 is two links, and full compression flattens both of them.
    return bool(root == 0 and cost == 2 and _forest(uf) == ([0, 0, 0, 0], [2, 0, 1, 0]))


def _binomial_tree_reaches_full_height(cls: Any) -> bool:
    """§4 + §7.5 — union by rank builds a tree of height exactly log2(n).

    If the build were flattening trees it should not (e.g. by unioning by size
    with a different tie-break, or by compressing during union beyond what find
    does) the deepest leaf would not sit at depth k.
    """
    for k in (3, 6, 10):
        uf = _binomial(cls, k)
        n = 1 << k
        if uf.component_count != 1 or uf.path_length(n - 1) != k:
            return False
        if uf.rank_of(uf.find(0)) != k:
            return False
    return True


def _find_flattens_the_entire_path(cls: Any) -> bool:
    """§3 + §7.3 — full compression: every node on the ascent path, not just x.

    Path halving and path splitting both pass a naive "did x get closer" check and
    both fail this one.
    """
    uf = _binomial(cls, 10)
    node = (1 << 10) - 1
    path = [node]
    while uf.parent_of(path[-1]) != path[-1]:
        path.append(uf.parent_of(path[-1]))
    if len(path) != 11:
        return False
    root = uf.find(node)
    if root != path[-1]:
        return False
    return all(uf.path_length(x) <= 1 for x in path)


def _second_find_is_free(cls: Any) -> bool:
    """§7.3 — the whole point of compression: the deep walk is paid once.

    A structure with no compression pays the full depth every single time, so this
    is the single sharpest discriminator in the oracle.
    """
    uf = _binomial(cls, 12)
    node = (1 << 12) - 1
    start = uf.pointer_hops
    uf.find(node)
    first = uf.pointer_hops - start
    uf.find(node)
    second = uf.pointer_hops - start - first
    return bool(first == 12 and second <= 1)


def _amortised_hop_budget_holds(cls: Any) -> bool:
    """§6 + §7.4 — the adversarial workload named in the Complexity section.

    Build the height-13 binomial tree, then sweep every element twice. A
    compressed forest pays at most n hops on the second sweep; an uncompressed one
    pays the sum of all depths, Θ(n log n), which here is over six times n.
    """
    k = 13
    n = 1 << k
    uf = _binomial(cls, k)
    build = uf.pointer_hops
    for x in range(n):
        uf.find(x)
    after_first = uf.pointer_hops
    for x in range(n):
        uf.find(x)
    after_second = uf.pointer_hops

    if build != 0:  # every union in the build joins two roots
        return False
    if after_second - after_first > n:
        return False
    operations = (n - 1) + 2 * n
    return bool(after_second <= 2 * (n + operations))


def _rank_strictly_increases_toward_the_root(cls: Any) -> bool:
    """§7.6 — rank[parent] > rank[x] for every non-root, under churn.

    This is the invariant that catches ranks being updated on the wrong node, or
    being touched by the compression pass (§4 forbids that outright).
    """
    for uf in (_churn(cls, 400, 1200, 20260811), _binomial(cls, 8)):
        for x in range(len(uf)):
            parent = uf.parent_of(x)
            if parent != x and uf.rank_of(parent) <= uf.rank_of(x):
                return False
    return True


def _height_and_rank_are_log_bounded(cls: Any) -> bool:
    """§7.5 — a rank-r root owns 2^r elements, so rank and depth are <= log2(n)."""
    for n, uf in ((400, _churn(cls, 400, 1500, 20260812)), (256, _binomial(cls, 8))):
        limit = n.bit_length() - 1
        for x in range(n):
            if uf.rank_of(x) > limit or uf.path_length(x) > limit:
                return False
    return True


def _star_union_never_raises_rank_above_one(cls: Any) -> bool:
    """§4 — union by rank, seen from the other side.

    Repeatedly unioning a rank-1 root with fresh singletons must leave the rank at
    1 forever. An implementation that increments unconditionally reaches rank 63
    here; one that ignores rank and always links a's root under b's builds a chain.
    """
    uf = cls(64)
    for i in range(1, 64):
        uf.union(0, i)
    if uf.rank_of(0) != 1 or uf.find(0) != 0:
        return False
    return all(uf.parent_of(x) == 0 and uf.rank_of(x) == 0 for x in range(1, 64))


def _forest_is_a_deterministic_function_of_the_operations(cls: Any) -> bool:
    """§7.8 — no set/dict iteration order may leak into the result."""
    first = _churn(cls, 300, 900, 20260813)
    second = _churn(cls, 300, 900, 20260813)
    return bool(
        _forest(first) == _forest(second)
        and first.pointer_hops == second.pointer_hops
        and first.component_count == second.component_count
        and first.components() == second.components()
    )


def _redundant_union_changes_nothing(cls: Any) -> bool:
    """§7.9 — returns False and leaves ranks, counts and the partition alone."""
    uf = _churn(cls, 60, 200, 20260814)
    for x in range(60):  # pre-compress so the two finds inside union are no-ops
        uf.find(x)
    pairs = [(a, b) for a in range(60) for b in range(60) if uf.connected(a, b)]
    before_forest, before_count = _forest(uf), uf.component_count
    for a, b in pairs[:200]:
        if uf.union(a, b) is not False:
            return False
    return bool(_forest(uf) == before_forest and uf.component_count == before_count)


def _partition_is_well_formed(cls: Any) -> bool:
    """§7.1 — disjoint, covering, inner lists ascending, outer by smallest member."""
    uf = _churn(cls, 120, 300, 20260815)
    groups = uf.components()
    flat = [x for g in groups for x in g]
    if sorted(flat) != list(range(120)) or len(flat) != 120:
        return False
    if any(g != sorted(g) or not g for g in groups):
        return False
    if [g[0] for g in groups] != sorted(g[0] for g in groups):
        return False
    return bool(len(groups) == uf.component_count)


def _sizes_agree_with_the_partition(cls: Any) -> bool:
    """§7.7 — component_size must be maintained, since rank carries no counts."""
    uf = _churn(cls, 150, 500, 20260816)
    groups = uf.components()
    if sum(len(g) for g in groups) != 150:
        return False
    for group in groups:
        for x in group:
            if uf.component_size(x) != len(group):
                return False
    return True


def _connected_is_an_equivalence_relation(cls: Any) -> bool:
    """§7.2 — reflexive, symmetric, transitive, and agrees with find."""
    uf = cls(12)
    for a, b in ((0, 1), (1, 2), (4, 5), (6, 7), (7, 8), (5, 8)):
        uf.union(a, b)
    for a in range(12):
        if not uf.connected(a, a):
            return False
        for b in range(12):
            if uf.connected(a, b) != (uf.find(a) == uf.find(b)):
                return False
            if uf.connected(a, b) != uf.connected(b, a):
                return False
            for c in range(12):
                if uf.connected(a, b) and uf.connected(b, c) and not uf.connected(a, c):
                    return False
    return True


def _finds_never_move_an_element_between_components(cls: Any) -> bool:
    """§7.9 — compression rewires the forest; it must not repartition it."""
    uf = _churn(cls, 200, 600, 20260817)
    before, count = uf.components(), uf.component_count
    rng = random.Random(20260818)
    for _ in range(2000):
        uf.find(rng.randrange(200))
    return bool(uf.components() == before and uf.component_count == count)


def _pure_observers_do_not_compress_or_count(cls: Any) -> bool:
    """§5 — parent_of, rank_of, path_length, component_count and len are pure.

    An implementation that routes path_length through find would flatten the tree
    and inflate the counter, destroying the oracle's ability to see depth at all.
    """
    uf = _binomial(cls, 9)
    node = (1 << 9) - 1
    before, hops = _forest(uf), uf.pointer_hops
    for _ in range(5):
        if uf.path_length(node) != 9:
            return False
        _ = uf.parent_of(node)
        _ = uf.rank_of(node)
        _ = uf.component_count
        _ = len(uf)
    return bool(_forest(uf) == before and uf.pointer_hops == hops)


def _add_extends_without_disturbing_existing_state(cls: Any) -> bool:
    """§8 — add() appends a singleton and returns its index."""
    uf = cls(3)
    uf.union(0, 1)
    before = _forest(uf)
    idx = uf.add()
    if idx != 3 or len(uf) != 4 or uf.component_count != 3:
        return False
    if _forest(uf)[0][:3] != before[0] or _forest(uf)[1][:3] != before[1]:
        return False
    if uf.path_length(3) != 0 or uf.rank_of(3) != 0 or uf.component_size(3) != 1:
        return False
    uf.union(3, 0)
    return bool(uf.component_size(3) == 3 and uf.component_count == 2)


def _degenerate_sizes(cls: Any) -> bool:
    """§8 — n == 0 and n == 1 are legal, not errors."""
    empty = cls(0)
    if len(empty) != 0 or empty.component_count != 0 or empty.components() != []:
        return False
    one = cls(1)
    return bool(
        len(one) == 1
        and one.component_count == 1
        and one.find(0) == 0
        and one.path_length(0) == 0
        and one.component_size(0) == 1
        and one.components() == [[0]]
    )


# ── §9 module-level constructors ─────────────────────────────────────────────


def _from_edges_matches_manual_unions(fn: Any) -> bool:
    """§9 — from_edges applies the edges in order with the §4 rule and nothing else.

    Compared against the same unions driven by hand, so an implementation that
    sorts or deduplicates the edges first — and thereby builds a different forest
    while reporting the same components — is caught.
    """
    edges = [(0, 1), (2, 3), (1, 3), (5, 6), (0, 5)]
    built = fn(8, edges)
    manual = type(built)(8)
    for a, b in edges:
        manual.union(a, b)
    return bool(
        _forest(built) == _forest(manual)
        and built.pointer_hops == manual.pointer_hops
        and built.component_count == 3
    )


def _agrees_with_an_independent_bfs(builder: Any) -> bool:
    """§7.1 — differential test against breadth-first search on random graphs.

    BFS shares no code with a disjoint-set forest, so agreement across hundreds of
    random graphs is real evidence rather than a restatement of the implementation.
    """
    rng = random.Random(20260819)
    for _ in range(120):
        n = rng.randrange(1, 40)
        edges = [
            (rng.randrange(n), rng.randrange(n)) for _ in range(rng.randrange(0, 2 * n + 1))
        ]
        uf = builder(n, edges)
        expected = _bfs_components(n, edges)
        if uf.components() != expected or uf.component_count != len(expected):
            return False
        for _ in range(10):
            a, b = rng.randrange(n), rng.randrange(n)
            same = any(a in g and b in g for g in expected)
            if uf.connected(a, b) is not same:
                return False
    return True


def _agrees_with_networkx(fn: Any) -> bool:
    """§7.1 + §10 — differential test against the library the build may not use.

    networkx is a dependency of this repository and is forbidden to the generated
    module by §10, which makes it the ideal external oracle: the answers must
    match, and the Prohibition below asserts the match was not obtained by calling
    it.
    """
    import networkx as nx

    rng = random.Random(20260820)
    for _ in range(60):
        n = rng.randrange(1, 30)
        edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(rng.randrange(0, n + 5))]
        graph = nx.Graph()
        graph.add_nodes_from(range(n))
        graph.add_edges_from(edges)
        expected = sorted((sorted(c) for c in nx.connected_components(graph)), key=lambda g: g[0])
        if fn(n, edges) != expected:
            return False
    return True


def _partition_is_invariant_under_edge_order(fn: Any) -> bool:
    """§7.1 — the partition depends on the edge set, not on the edge sequence.

    The forest shape legitimately differs; the reported components must not.
    """
    edges = [(0, 4), (1, 2), (4, 7), (2, 8), (3, 3), (5, 6), (8, 1), (6, 9)]
    expected = fn(10, edges)
    rng = random.Random(20260821)
    for _ in range(25):
        shuffled = edges[:]
        rng.shuffle(shuffled)
        if fn(10, shuffled) != expected:
            return False
    return bool(expected == [[0, 4, 7], [1, 2, 8], [3], [5, 6, 9]])


ORACLE = Oracle(
    whitepaper="11_union_find.md",
    package_hint="union",
    required_names=["UnionFind", "from_edges", "connected_components"],
    cases=[
        Case(
            target="UnionFind",
            call=False,
            check=_fresh_structure_is_all_singletons,
            description="§2 a fresh structure is n singletons with rank 0 and 0 hops",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_worked_example_forest_shape,
            description="§4 the mandated tie-break fixes the exact parent/rank arrays",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_binomial_tree_reaches_full_height,
            description="§7.5 union by rank builds a tree of height exactly log2(n)",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_find_flattens_the_entire_path,
            description="§3 find compresses every node on the path, not just x",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_second_find_is_free,
            description="§7.3 a repeated find costs at most one pointer hop",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_amortised_hop_budget_holds,
            description="§6 the adversarial workload stays inside the hop budget",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_rank_strictly_increases_toward_the_root,
            description="§7.6 rank strictly increases along every parent pointer",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_height_and_rank_are_log_bounded,
            description="§7.5 rank and depth never exceed floor(log2(n))",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_star_union_never_raises_rank_above_one,
            description="§4 unioning a root with singletons leaves its rank at 1",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_forest_is_a_deterministic_function_of_the_operations,
            description="§7.8 identical operation sequences give identical forests",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_redundant_union_changes_nothing,
            description="§7.9 union on connected elements returns False and is inert",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_partition_is_well_formed,
            description="§7.1 components() is a disjoint, covering, ordered partition",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_sizes_agree_with_the_partition,
            description="§7.7 component_size agrees with components() for every element",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_connected_is_an_equivalence_relation,
            description="§7.2 connected is reflexive, symmetric, transitive, find-consistent",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_finds_never_move_an_element_between_components,
            description="§7.9 compression leaves the partition untouched",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_pure_observers_do_not_compress_or_count,
            description="§5 parent_of/rank_of/path_length neither compress nor count",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_add_extends_without_disturbing_existing_state,
            description="§8 add() appends a singleton and preserves existing state",
        ),
        Case(
            target="UnionFind",
            call=False,
            check=_degenerate_sizes,
            description="§8 n == 0 and n == 1 are legal",
        ),
        Case(
            target="from_edges",
            call=False,
            check=_agrees_with_an_independent_bfs,
            description="§7.1 differential agreement with breadth-first search",
        ),
        Case(
            target="from_edges",
            call=False,
            check=_from_edges_matches_manual_unions,
            description="§9 from_edges is exactly the edges unioned in order",
        ),
        Case(
            target="connected_components",
            call=False,
            check=_agrees_with_networkx,
            description="§7.1 differential agreement with networkx (which §10 forbids)",
        ),
        Case(
            target="connected_components",
            call=False,
            check=_partition_is_invariant_under_edge_order,
            description="§7.1 the partition does not depend on edge order",
        ),
        Case(
            target="connected_components",
            args=(6, [(0, 1), (1, 2), (4, 5)]),
            expected=[[0, 1, 2], [3], [4, 5]],
            description="§9 worked example: three components, ordered by least member",
        ),
        Case(
            target="connected_components",
            args=(3, []),
            expected=[[0], [1], [2]],
            description="§9 no edges leaves every element isolated",
        ),
        Case(
            target="connected_components",
            args=(0, []),
            expected=[],
            description="§8 zero elements gives the empty partition",
        ),
        Case(
            target="connected_components",
            args=(3, [(1, 1), (2, 2), (0, 1), (0, 1)]),
            expected=[[0, 1], [2]],
            description="§8 self-loops and duplicate edges are no-ops",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="UnionFind",
            args=(-1,),
            exc_name="ValueError",
            description="§8 a negative element count raises ValueError",
        ),
        ErrorCase(
            target="UnionFind",
            args=("5",),
            exc_name="TypeError",
            description="§8 a string element count raises TypeError",
        ),
        ErrorCase(
            target="UnionFind",
            args=(4.0,),
            exc_name="TypeError",
            description="§8 a float element count raises TypeError",
        ),
        ErrorCase(
            target="UnionFind",
            args=(True,),
            exc_name="TypeError",
            description="§8 bool is rejected despite subclassing int",
        ),
        ErrorCase(
            target="from_edges",
            args=(-3, []),
            exc_name="ValueError",
            description="§8 from_edges validates the element count",
        ),
        ErrorCase(
            target="from_edges",
            args=(3, [(0, 3)]),
            exc_name="IndexError",
            description="§8 an element at n is out of range",
        ),
        ErrorCase(
            target="from_edges",
            args=(3, [(0, -1)]),
            exc_name="IndexError",
            description="§8 negative indices are out of range, not counted from the end",
        ),
        ErrorCase(
            target="from_edges",
            args=(3, [(0, "x")]),
            exc_name="TypeError",
            description="§8 a non-integer element raises TypeError",
        ),
        ErrorCase(
            target="from_edges",
            args=(3, [(0, True)]),
            exc_name="TypeError",
            description="§8 a bool element raises TypeError",
        ),
        ErrorCase(
            target="from_edges",
            args=(3, [(0, 1, 2)]),
            exc_name="ValueError",
            match="pair",
            description="§8 an edge that is not a pair raises ValueError",
        ),
        ErrorCase(
            target="connected_components",
            args=(3, [(3, 0)]),
            exc_name="IndexError",
            description="§8 connected_components validates elements too",
        ),
        ErrorCase(
            target="connected_components",
            args=(2, [(0,)]),
            exc_name="ValueError",
            match="pair",
            description="§8 a one-element edge raises ValueError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§10 forbids delegating to any graph library — a wrapper around "
                "networkx.connected_components or scipy.sparse.csgraph would answer "
                "every connectivity query correctly while implementing none of the "
                "forest, the ranks, or the compression this specification is about"
            ),
            imports=(
                "networkx",
                "scipy",
                "numpy",
                "pandas",
                "igraph",
                "rustworkx",
                "retworkx",
                "graph_tool",
                "networkit",
                "graphlib",
            ),
        ),
    ],
)
