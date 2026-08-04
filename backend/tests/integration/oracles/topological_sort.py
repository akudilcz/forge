"""Oracle for whitepapers/04_topological_sort.md.

Authored from the whitepaper only. No agent sees this file, and it is never
written into the workspace, so it is an independent check on whether FORGE built
what was specified rather than something self-consistently wrong.

Topological sort is unusually easy to get *nearly* right. A DFS-based ordering,
a set-driven ready frontier, and in-degree counting that double-counts duplicate
edges all produce plausible answers on hand-written examples. So the centre of
gravity here is the properties no single call exhibits: the order must be the
lexicographically-smallest one (§3.1), it must be identical for two dicts that
describe the same graph in a different insertion order, the reported cycle must
be a *real* walk in the graph, and neither pass may recurse.

``graphlib`` and an independent reachability closure are used here as
differential oracles; §11 forbids the generated code from using ``graphlib``.

Section references below point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

import functools
import graphlib
import random
from collections.abc import Callable
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _guarded(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn a raise from the generated code into a clean rejection.

    ``run_oracle`` wraps the *call* of a ``Case`` but not its ``check``, so a
    ``RecursionError`` from a recursive implementation, or a ``TypeError`` from
    a mis-signalled cycle, would abort the whole oracle run and hide every other
    failure. The conformance test still catches a bug in this file's own
    helpers, because a swallowed exception there rejects the reference too.
    """

    @functools.wraps(check)
    def guarded(target: Any) -> bool:
        try:
            return check(target)
        except Exception:  # noqa: BLE001 — any raise means the property does not hold
            return False

    return guarded


# ── graph fixtures (functions, so every case gets a fresh, unshared mapping) ──


def _diamond() -> dict[Any, list[Any]]:
    return {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}


def _chain() -> dict[Any, list[Any]]:
    return {"a": ["b"], "b": ["c"], "c": []}


def _self_loop() -> dict[Any, list[Any]]:
    return {"a": ["a"]}


def _two_cycle() -> dict[Any, list[Any]]:
    return {"a": ["b"], "b": ["a"]}


def _three_cycle() -> dict[Any, list[Any]]:
    """Distinctive node names so the §10 ``__str__`` check is not vacuous."""
    return {"alpha": ["beta"], "beta": ["gamma"], "gamma": ["alpha"]}


def _cycle_with_tail() -> dict[Any, list[Any]]:
    """A cycle plus acyclic fringe — the residual graph has a dead-end node."""
    return {"a": ["b"], "b": ["c"], "c": ["b"], "d": ["a"], "e": []}


def _tri_scc() -> dict[Any, list[Any]]:
    """Condensation is the path {a,b,c} -> {d,e,f} -> {g}, so §5's order is unique."""
    return {
        "a": ["b"],
        "b": ["c"],
        "c": ["a", "d"],
        "d": ["e"],
        "e": ["f"],
        "f": ["d", "g"],
        "g": [],
    }


def _deep_path(size: int) -> dict[Any, list[Any]]:
    graph: dict[Any, list[Any]] = {i: [i + 1] for i in range(size - 1)}
    graph[size - 1] = []
    return graph


_RANKS = {"a": 3, "b": 1, "c": 2}


def _rank(node: Any) -> Any:
    return _RANKS[node]


# ── independent graph utilities (the oracle's own reference semantics) ───────


def _nodes_of(graph: Any) -> list[Any]:
    """§2 — nodes appearing only as successors join the node set implicitly."""
    out: list[Any] = []
    seen: set[Any] = set()
    for u, successors in graph.items():
        if u not in seen:
            seen.add(u)
            out.append(u)
        for v in successors:
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _successor_sets(graph: Any) -> dict[Any, set[Any]]:
    """§2 — duplicate edges collapse."""
    succ: dict[Any, set[Any]] = {n: set() for n in _nodes_of(graph)}
    for u, successors in graph.items():
        succ[u].update(successors)
    return succ


def _edges_of(graph: Any) -> set[tuple[Any, Any]]:
    return {(u, v) for u, outs in _successor_sets(graph).items() for v in outs}


def _reach(succ: dict[Any, set[Any]], start: Any) -> set[Any]:
    """Nodes reachable in one or more steps — includes ``start`` iff it is on a cycle."""
    seen: set[Any] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        for v in succ[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def _scc_sets(graph: Any) -> set[frozenset[Any]]:
    """Mutual-reachability partition. Deliberately O(n^3) and obviously correct."""
    nodes = _nodes_of(graph)
    succ = _successor_sets(graph)
    reach = {n: _reach(succ, n) for n in nodes}
    components: set[frozenset[Any]] = set()
    for n in nodes:
        components.add(frozenset({n} | {m for m in nodes if m in reach[n] and n in reach[m]}))
    return components


def _canonical_order(graph: Any) -> list[Any]:
    """§3.1 — always emit the smallest ready node, i.e. the lex-smallest order."""
    nodes = _nodes_of(graph)
    succ = _successor_sets(graph)
    indegree = dict.fromkeys(nodes, 0)
    for u in nodes:
        for v in succ[u]:
            indegree[v] += 1
    ready = sorted(n for n in nodes if indegree[n] == 0)
    out: list[Any] = []
    while ready:
        u = ready.pop(0)
        out.append(u)
        for v in sorted(succ[u]):
            indegree[v] -= 1
            if indegree[v] == 0:
                ready.append(v)
        ready.sort()
    return out


def _longest_path(graph: Any) -> int:
    """§6 — edges on the longest path, relaxed along a topological order."""
    succ = _successor_sets(graph)
    distance = dict.fromkeys(_nodes_of(graph), 0)
    for u in _canonical_order(graph):
        for v in succ[u]:
            if distance[u] + 1 > distance[v]:
                distance[v] = distance[u] + 1
    return max(distance.values(), default=0)


def _stdlib_is_acyclic(graph: Any) -> bool:
    """Differential oracle. ``TopologicalSorter`` wants *predecessors*, so invert."""
    predecessors: dict[Any, list[Any]] = {n: [] for n in _nodes_of(graph)}
    for u, successors in graph.items():
        for v in successors:
            predecessors[v].append(u)
    try:
        graphlib.TopologicalSorter(predecessors).prepare()
    except graphlib.CycleError:
        return False
    return True


def _is_valid_order(graph: Any, order: Any) -> bool:
    """§8.1 + §8.2 — every node exactly once, and every edge respected."""
    if not isinstance(order, list):
        return False
    nodes = _nodes_of(graph)
    if len(order) != len(nodes) or set(order) != set(nodes):
        return False
    position = {n: i for i, n in enumerate(order)}
    return all(position[u] < position[v] for u, v in _edges_of(graph))


def _cycle_is_sound(graph: Any, cycle: Any) -> bool:
    """§8.4 + §4 — consecutive nodes are genuinely adjacent, and none repeats."""
    if not isinstance(cycle, list) or not cycle:
        return False
    if len(set(cycle)) != len(cycle):
        return False
    edges = _edges_of(graph)
    return all((cycle[i], cycle[(i + 1) % len(cycle)]) in edges for i in range(len(cycle)))


def _random_dag(rng: random.Random, size: int, density: float) -> dict[Any, list[Any]]:
    """Edges only along a hidden linear extension, then relabelled and shuffled."""
    labels = list(range(size))
    rng.shuffle(labels)
    graph: dict[Any, list[Any]] = {labels[i]: [] for i in range(size)}
    for i in range(size):
        for j in range(i + 1, size):
            if rng.random() < density:
                graph[labels[i]].append(labels[j])
    return graph


def _random_digraph(rng: random.Random, size: int, density: float) -> dict[Any, list[Any]]:
    """Unrestricted, so self-loops and multi-node cycles both occur."""
    graph: dict[Any, list[Any]] = {u: [] for u in range(size)}
    for u in range(size):
        for v in range(size):
            if rng.random() < density:
                graph[u].append(v)
    return graph


# ── checks ───────────────────────────────────────────────────────────────────


@_guarded
def _matches_the_lexicographic_order(fn: Any) -> bool:
    """§3.1 — the default tie-break makes exactly one order admissible.

    Any implementation that breaks ties by insertion order, hash order or set
    iteration order produces a *valid* topological order that is not this one.
    """
    rng = random.Random(2718)
    for _ in range(40):
        graph = _random_dag(rng, 12, 0.25)
        if fn(graph) != _canonical_order(graph):
            return False
    return True


@_guarded
def _identical_for_equal_graphs_built_differently(fn: Any) -> bool:
    """§8.3 — determinism. Same graph, eight different dict/list layouts."""
    rng = random.Random(1618)
    base = _random_dag(rng, 14, 0.22)
    edges = sorted(_edges_of(base))
    nodes = sorted(_nodes_of(base))
    results: list[Any] = []
    for _ in range(8):
        pairs = list(edges)
        rng.shuffle(pairs)
        keys = list(nodes)
        rng.shuffle(keys)
        graph: dict[Any, list[Any]] = {k: [] for k in keys}
        for u, v in pairs:
            graph[u].append(v)
        results.append(fn(graph))
    return bool(all(r == results[0] for r in results) and _is_valid_order(base, results[0]))


@_guarded
def _leaves_the_input_graph_untouched(fn: Any) -> bool:
    """§3.1 — "topological_sort is a pure function"; in-place dedup is forbidden."""
    graph: dict[Any, list[Any]] = {"a": ["b", "b", "c"], "b": ["d"], "c": ["d"], "d": []}
    snapshot = {k: list(v) for k, v in graph.items()}
    fn(graph)
    if list(graph) != list(snapshot):
        return False
    return all(list(graph[k]) == snapshot[k] for k in snapshot)


@_guarded
def _accepts_non_list_successor_iterables(fn: Any) -> bool:
    """§2 — the mapping's values are "an iterable of its successors"."""
    graph: dict[Any, Any] = {"a": {"b", "c"}, "b": ("d",), "c": frozenset({"d"}), "d": ()}
    return bool(fn(graph) == ["a", "b", "c", "d"])


@_guarded
def _incomparable_nodes_with_edges_still_order(fn: Any) -> bool:
    """§9 — mixed int/str must not raise TypeError anywhere on the path."""
    graph: dict[Any, list[Any]] = {"b": [2], 2: ["a"], 1: ["b"], "a": []}
    return _is_valid_order(graph, fn(graph))


@_guarded
def _orders_a_ten_thousand_node_path(fn: Any) -> bool:
    """§9 — Kahn's algorithm must be iterative; a DFS ordering raises here."""
    return bool(fn(_deep_path(10_000)) == list(range(10_000)))


@_guarded
def _tie_breaker_is_consulted_a_linear_number_of_times(fn: Any) -> bool:
    """§7 — O(V + E log V).

    A frontier scanned with ``min(ready, key=...)`` on every iteration is
    functionally perfect and quadratic: on this edge-free graph it evaluates the
    key ~V^2/2 = 2,000,000 times against a priority queue's V.
    """
    size = 2000
    graph: dict[Any, list[Any]] = {i: [] for i in range(size)}
    calls = [0]

    def counting_key(node: Any) -> Any:
        calls[0] += 1
        return node

    result = fn(graph, tie_breaker=counting_key)
    return bool(result == list(range(size)) and calls[0] <= 40 * size)


@_guarded
def _cycle_error_carries_a_sound_witness(fn: Any) -> bool:
    """§8.4 + §10 — the exception names a real cycle, not merely "cyclic"."""
    for graph in (_self_loop(), _two_cycle(), _three_cycle(), _cycle_with_tail()):
        try:
            fn(graph)
        except Exception as exc:  # noqa: BLE001 — any raise is inspected below
            cycle = getattr(exc, "cycle", None)
            if not cycle or not _cycle_is_sound(graph, cycle):
                return False
            text = str(exc).lower()
            if "cycle" not in text or not all(str(n).lower() in text for n in cycle):
                return False
        else:
            return False
    return True


@_guarded
def _find_cycle_agrees_with_the_stdlib(fn: Any) -> bool:
    """§8.5 — never claims a cyclic graph is acyclic, and never invents a cycle."""
    rng = random.Random(777)
    for _ in range(120):
        graph = _random_digraph(rng, 8, 0.16)
        cycle = fn(graph)
        if _stdlib_is_acyclic(graph):
            if cycle is not None:
                return False
        elif not _cycle_is_sound(graph, cycle):
            return False
    return True


@_guarded
def _find_cycle_reports_the_cycle_behind_a_fringe(fn: Any) -> bool:
    """§4 — the residual graph contains dead ends; a naive forward walk stalls."""
    graph = _cycle_with_tail()
    return _cycle_is_sound(graph, fn(graph))


@_guarded
def _acyclicity_matches_the_stdlib(fn: Any) -> bool:
    """§6 + §8.5 — is_acyclic must never raise, on cyclic graphs least of all."""
    rng = random.Random(31337)
    for _ in range(120):
        graph = _random_digraph(rng, 8, 0.16)
        if bool(fn(graph)) != _stdlib_is_acyclic(graph):
            return False
    return True


@_guarded
def _scc_is_a_reverse_topological_partition(fn: Any) -> bool:
    """§5 + §8.6 — partition, within-component ordering, condensation order."""
    rng = random.Random(90210)
    for _ in range(40):
        graph = _random_digraph(rng, 9, 0.18)
        components = fn(graph)
        flat: list[Any] = []
        for component in components:
            if list(component) != sorted(component):
                return False
            flat.extend(component)
        nodes = _nodes_of(graph)
        if len(flat) != len(nodes) or set(flat) != set(nodes):
            return False
        if {frozenset(c) for c in components} != _scc_sets(graph):
            return False
        where = {n: i for i, component in enumerate(components) for n in component}
        for u, v in _edges_of(graph):
            if where[u] != where[v] and where[u] <= where[v]:
                return False
    return True


@_guarded
def _scc_handles_a_ten_thousand_node_path(fn: Any) -> bool:
    """§5 + §9 — Tarjan's must be iterative; each node is its own component."""
    components = fn(_deep_path(10_000))
    if len(components) != 10_000:
        return False
    return all(components[i] == [9_999 - i] for i in range(10_000))


@_guarded
def _longest_path_matches_relaxation(fn: Any) -> bool:
    """§6 — differential check on random DAGs."""
    rng = random.Random(5150)
    for _ in range(30):
        graph = _random_dag(rng, 12, 0.2)
        if fn(graph) != _longest_path(graph):
            return False
    return True


@_guarded
def _descendants_match_reachability(fn: Any) -> bool:
    """§6 — transitive closure, self included exactly when the node is on a cycle."""
    rng = random.Random(4242)
    for _ in range(30):
        graph = _random_digraph(rng, 8, 0.16)
        succ = _successor_sets(graph)
        for node in _nodes_of(graph):
            if set(fn(graph, node)) != _reach(succ, node):
                return False
    return True


@_guarded
def _ancestors_match_reverse_reachability(fn: Any) -> bool:
    """§6 — the mirror of the above, over the reversed graph."""
    rng = random.Random(4243)
    for _ in range(30):
        graph = _random_digraph(rng, 8, 0.16)
        succ = _successor_sets(graph)
        nodes = _nodes_of(graph)
        reach = {n: _reach(succ, n) for n in nodes}
        for node in nodes:
            if set(fn(graph, node)) != {m for m in nodes if node in reach[m]}:
                return False
    return True


def _is_the_two_node_cycle(result: Any) -> bool:
    return bool(isinstance(result, list) and len(result) == 2 and set(result) == {"a", "b"})


ORACLE = Oracle(
    whitepaper="04_topological_sort.md",
    package_hint="topo",
    required_names=[
        "CyclicGraphError",
        "topological_sort",
        "is_acyclic",
        "find_cycle",
        "strongly_connected_components",
        "longest_path_length",
        "ancestors",
        "descendants",
    ],
    cases=[
        # §9 — boundary graphs
        Case(
            target="topological_sort",
            args=({},),
            expected=[],
            description="§9 empty graph returns an empty list, not an error",
        ),
        Case(
            target="topological_sort",
            args=({"a": []},),
            expected=["a"],
            description="§9 single node with no edges",
        ),
        Case(
            target="topological_sort",
            args=({"a": ["b"]},),
            expected=["a", "b"],
            description="§9 a successor missing from the keys joins the node set",
        ),
        # §3.1 — the default tie-break is the whole point of choosing Kahn
        Case(
            target="topological_sort",
            args=(_diamond(),),
            expected=["a", "b", "c", "d"],
            description="§3.1 diamond resolves ties by ascending node",
        ),
        Case(
            target="topological_sort",
            args=({"a": ["b"], "x": ["y"], "b": [], "y": []},),
            expected=["a", "b", "x", "y"],
            description="§9 disconnected components interleave by tie-break",
        ),
        Case(
            target="topological_sort",
            args=({"a": [], "b": [], "c": []},),
            kwargs={"tie_breaker": _rank},
            expected=["b", "c", "a"],
            description="§3.1 a caller-supplied tie_breaker overrides the default",
        ),
        # §2 — duplicate edges. Counting them corrupts the in-degree either way:
        # count-dupes/decrement-once strands the node, the reverse drives it
        # negative. Both surface as a bogus CyclicGraphError.
        Case(
            target="topological_sort",
            args=({"a": ["b", "b", "b"], "b": []},),
            expected=["a", "b"],
            description="§2 repeated successors of one node count as one edge",
        ),
        Case(
            target="topological_sort",
            args=({"a": ["c", "c"], "b": ["c", "c", "c"], "c": []},),
            expected=["a", "b", "c"],
            description="§2 duplicate edges from several sources leave in-degree at 2",
        ),
        # §9 — incomparable nodes
        Case(
            target="topological_sort",
            args=({"z": [], 1: [], "a": [], 2: []},),
            expected=["z", 1, "a", 2],
            description="§9 mixed int/str nodes fall back to insertion order",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_incomparable_nodes_with_edges_still_order,
            description="§9 incomparable nodes with edges order without TypeError",
        ),
        # §2, §3.1, §7, §8 — properties no single call exhibits
        Case(
            target="topological_sort",
            call=False,
            check=_matches_the_lexicographic_order,
            description="§3.1 default order is the lexicographically smallest one",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_identical_for_equal_graphs_built_differently,
            description="§8.3 equal graphs in any dict order give an identical list",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_leaves_the_input_graph_untouched,
            description="§3.1 the input mapping is not mutated (pure function)",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_accepts_non_list_successor_iterables,
            description="§2 successors may be any iterable (set, tuple, frozenset)",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_orders_a_ten_thousand_node_path,
            description="§9 10,000-node path orders without RecursionError",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_tie_breaker_is_consulted_a_linear_number_of_times,
            description="§7 the ready frontier is a queue, not a re-scanned list",
        ),
        Case(
            target="topological_sort",
            call=False,
            check=_cycle_error_carries_a_sound_witness,
            description="§10 CyclicGraphError.cycle is a real walk and __str__ names it",
        ),
        # §4, §9 — cycle recovery
        Case(
            target="find_cycle",
            args=(_self_loop(),),
            expected=["a"],
            description="§9 a self-loop is reported as the length-1 cycle [u]",
        ),
        Case(
            target="find_cycle",
            args=(_two_cycle(),),
            check=_is_the_two_node_cycle,
            description="§9 a -> b -> a is reported as a length-2 cycle",
        ),
        Case(
            target="find_cycle",
            args=(_diamond(),),
            expected=None,
            description="§10 find_cycle returns None on a DAG",
        ),
        Case(
            target="find_cycle",
            call=False,
            check=_find_cycle_reports_the_cycle_behind_a_fringe,
            description="§4 finds the cycle although the residual graph has a dead end",
        ),
        Case(
            target="find_cycle",
            call=False,
            check=_find_cycle_agrees_with_the_stdlib,
            description="§8.4/§8.5 sound and complete against 120 random digraphs",
        ),
        # §6 — is_acyclic
        Case(
            target="is_acyclic",
            args=(_diamond(),),
            expected=True,
            description="§6 is_acyclic is True for a DAG",
        ),
        Case(
            target="is_acyclic",
            args=(_three_cycle(),),
            expected=False,
            description="§6 is_acyclic is False, and does not raise, for a cycle",
        ),
        Case(
            target="is_acyclic",
            args=(_self_loop(),),
            expected=False,
            description="§2 a self-loop makes the graph non-orderable",
        ),
        Case(
            target="is_acyclic",
            call=False,
            check=_acyclicity_matches_the_stdlib,
            description="§8.5 matches graphlib on 120 random digraphs",
        ),
        # §5 — strongly connected components
        Case(
            target="strongly_connected_components",
            args=({},),
            expected=[],
            description="§9 empty graph has no components",
        ),
        Case(
            target="strongly_connected_components",
            args=({"a": []},),
            expected=[["a"]],
            description="§5 an acyclic node forms a singleton component",
        ),
        Case(
            target="strongly_connected_components",
            args=(_tri_scc(),),
            expected=[["g"], ["d", "e", "f"], ["a", "b", "c"]],
            description="§5 components in reverse topological order of the condensation",
        ),
        Case(
            target="strongly_connected_components",
            call=False,
            check=_scc_is_a_reverse_topological_partition,
            description="§8.6 partition + ordering, differential over 40 random digraphs",
        ),
        Case(
            target="strongly_connected_components",
            call=False,
            check=_scc_handles_a_ten_thousand_node_path,
            description="§5 Tarjan is iterative: 10,000-node path yields 10,000 singletons",
        ),
        # §6 — longest_path_length
        Case(
            target="longest_path_length",
            args=(_chain(),),
            expected=2,
            description="§6 longest path counts edges, not nodes",
        ),
        Case(
            target="longest_path_length",
            args=(_diamond(),),
            expected=2,
            description="§6 both diamond branches have length 2",
        ),
        Case(
            target="longest_path_length",
            args=({"a": []},),
            expected=0,
            description="§6 a lone node has a zero-edge longest path",
        ),
        Case(
            target="longest_path_length",
            args=(_deep_path(10_000),),
            expected=9_999,
            description="§9 relaxation over a 10,000-node path stays iterative",
        ),
        Case(
            target="longest_path_length",
            call=False,
            check=_longest_path_matches_relaxation,
            description="§6 differential against relaxation over 30 random DAGs",
        ),
        # §6 — transitive closures
        Case(
            target="descendants",
            args=({"a": ["b"], "b": ["c"], "c": [], "d": ["c"]}, "a"),
            expected={"b", "c"},
            description="§6 descendants exclude the node itself in a DAG",
        ),
        Case(
            target="ancestors",
            args=({"a": ["b"], "b": ["c"], "c": [], "d": ["c"]}, "c"),
            expected={"a", "b", "d"},
            description="§6 ancestors gather every predecessor transitively",
        ),
        Case(
            target="descendants",
            args=(_two_cycle(), "a"),
            expected={"a", "b"},
            description="§6 a node on a cycle is its own descendant",
        ),
        Case(
            target="descendants",
            call=False,
            check=_descendants_match_reachability,
            description="§6 descendants match forward reachability on random digraphs",
        ),
        Case(
            target="ancestors",
            call=False,
            check=_ancestors_match_reverse_reachability,
            description="§6 ancestors match reverse reachability on random digraphs",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="topological_sort",
            args=(_three_cycle(),),
            exc_name="CyclicGraphError",
            match="cycle",
            description="§3 a cyclic graph raises CyclicGraphError naming the cycle",
        ),
        ErrorCase(
            target="topological_sort",
            args=(_self_loop(),),
            exc_name="CyclicGraphError",
            description="§9 a self-loop raises rather than dropping the node",
        ),
        ErrorCase(
            target="topological_sort",
            args=(_cycle_with_tail(),),
            exc_name="ValueError",
            description="§10 CyclicGraphError subclasses ValueError",
        ),
        ErrorCase(
            target="longest_path_length",
            args=(_three_cycle(),),
            exc_name="CyclicGraphError",
            description="§6 longest_path_length raises CyclicGraphError on a cycle",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§11 forbids graphlib and networkx — Kahn's algorithm, Tarjan's "
                "algorithm and the cycle witness are the deliverable, and "
                "graphlib.TopologicalSorter would satisfy every functional check "
                "while implementing none of them"
            ),
            imports=("graphlib", "networkx"),
        ),
    ],
)
