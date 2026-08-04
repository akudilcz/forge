"""A* search library forge.md — multi-module spec for stress-testing the pipeline.

Three modules (graph, heuristics, search) with clear interfaces, multiple
data structures, and non-trivial algorithmic requirements.  Exercises:
- 4+ PARAs
- 8+ HLRs across functional areas
- 2-3 MODULEs with cross-module contracts
- 10-15 LLRs with architectural constraints
- Non-trivial DESIGN decisions (priority queue, neighbor generation)
"""

FORGE_MD = """\
# Optimised A* Search Library

## Overview

This document specifies a Python library that implements the A* search
algorithm for finding shortest paths on weighted graphs.  The library
supports directed and undirected graphs with non-negative edge weights
and provides pluggable heuristic functions.

## Graph Representation

The system shall represent a graph as an adjacency list stored in a
Python dictionary.  Each key is a node identifier (string).  Each value
is a list of (neighbor_id, weight) tuples where weight is a non-negative
float.

The system shall provide a Graph class with the following methods:

- add_edge(source, target, weight) — add a weighted edge.  For undirected
  graphs, this adds edges in both directions.  The weight must be
  non-negative; a ValueError shall be raised otherwise.

- neighbors(node) — return the list of (neighbor_id, weight) tuples for
  a node.  Return an empty list if the node has no outgoing edges.

- has_node(node) — return True if the node exists in the graph.

- node_count() — return the total number of nodes.

- edge_count() — return the total number of directed edges.

The Graph class shall accept a directed flag in its constructor
(default False for undirected).  When directed is False, add_edge
shall automatically add the reverse edge.

## Heuristic Functions

The system shall provide a HeuristicRegistry that maps string names to
callable heuristic functions.  Each heuristic takes two arguments
(current_node, goal_node) and returns a non-negative float estimate of
the remaining cost.

The system shall include the following built-in heuristics:

- "zero" — always returns 0.0 (reduces A* to Dijkstra).

- "manhattan" — computes the Manhattan distance assuming nodes are
  (x, y) tuples.  Raises TypeError if node values are not numeric
  tuples of length 2.

- "euclidean" — computes the Euclidean distance assuming nodes are
  (x, y) tuples.  Raises TypeError if node values are not numeric
  tuples of length 2.

The system shall allow registering custom heuristics via
register(name, fn) and retrieving them via get(name).  A KeyError
shall be raised if a requested heuristic name is not registered.

All heuristics must be admissible (never overestimate).  The library
does not enforce this at runtime but documents it as a contract.

## Search Algorithm

The system shall implement A* search in a PathFinder class that
accepts a Graph and a heuristic function.

PathFinder.find_path(start, goal) shall return a tuple of
(path, total_cost) where path is a list of node IDs from start to
goal inclusive, and total_cost is the sum of edge weights along the
path.  If no path exists, the method shall return (None, float('inf')).

The search shall use a min-heap priority queue ordered by f = g + h
where g is the cost from start and h is the heuristic estimate to goal.

The system shall track visited nodes to avoid re-expansion.  A node
shall not be expanded more than once.

The system shall raise a ValueError if start or goal is not in the graph.

The system shall handle the trivial case where start equals goal by
returning ([start], 0.0) without any search.

## Performance Requirements

The system shall use Python's heapq module for the priority queue
to achieve O(log n) push and pop operations.

The system shall represent the g-cost table as a dictionary for O(1)
amortised lookups.

The system shall terminate in finite time for any finite graph with
non-negative weights.

## Error Handling

The system shall raise ValueError for invalid edge weights (negative
or non-numeric), invalid node references in search (start or goal
not in graph), and invalid heuristic names (not registered).

The system shall raise TypeError if a heuristic function receives
nodes in an incompatible format (e.g. manhattan with string nodes).

All exceptions shall include descriptive messages indicating what
was invalid and why.
"""
