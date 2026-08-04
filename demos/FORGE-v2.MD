# Hybrid A* Path Planning with Lattice Motion Primitives and Exact Euclidean Distance Transform Obstacle Inflation

Algorithm Specification for Code Generation Validation

## Abstract

This document specifies a grid-based Hybrid A* path planner for non-holonomic vehicles operating on a 2D occupancy grid. The algorithm searches over a three-dimensional state space (x, y, θ) where position is discretized to integer grid cells and heading is quantized into Nθ uniform angular bins. Obstacle inflation uses a Felzenszwalb–Huttenlocher exact Euclidean distance transform (EDT), and the search is guided by a backward Dijkstra heuristic computed on the 2D grid. Motion is generated through a precomputed set of lattice primitives derived from constant-curvature arcs, with collision checking performed along rasterized arc footprints. The planner returns a sequence of (x, y, θ) poses from start to goal if a feasible path exists, or reports failure otherwise.

## 1. Overview and Design Rationale

Classical A* on a 2D grid finds shortest paths but ignores vehicle kinematics. A true non-holonomic planner (e.g., full state-lattice search or RRT*) can be prohibitively expensive on fine grids. Hybrid A* bridges this gap: it searches in the continuous (x, y, θ) space but indexes states by their nearest discrete cell and heading bin, ensuring that at most one state per (cell, bin) tuple is expanded. This yields tractable complexity while producing paths that respect steering constraints.

The algorithm described here makes the following design choices:

- Heading is quantized into 72 bins of 5° each, covering the full circle.

- Five steering actions per heading bin: two sharp turns (±2 bins), two gentle turns (±1 bin), and straight (0 bins).

- Motion primitives are constant-curvature arcs of a fixed step length, precomputed once.

- Obstacle inflation is performed exactly via the Felzenszwalb–Huttenlocher EDT, converting a binary occupancy grid into a binary inflated grid by thresholding the squared-distance field.

- The heuristic is a full backward Dijkstra flood from the goal on the inflated 2D grid with 8-connected movement, providing an admissible and consistent lower bound.

- Goal matching allows a configurable angular tolerance in heading bins.

## 2. State Space Definition

The planner operates on a discrete state space with the following structure:

### 2.1 Grid and Heading Discretization

Let the occupancy grid have dimensions W × H cells. Each cell is identified by integer coordinates (x, y) with 0 ≤ x < W and 0 ≤ y < H. Heading is quantized into Nθ = 72 bins. The angle corresponding to bin t is:

θ(t) = 2π · t / Nθ

A continuous heading θ maps to its nearest bin via rounding: t = round(θ · Nθ / 2π) mod Nθ. All bin arithmetic wraps modulo Nθ.

### 2.2 State Indexing

Each discrete state (x, y, t) is packed into a single 32-bit integer via the mapping:

s = (y · W + x) · Nθ + t

The total state count is W × H × Nθ, which must fit in 32 bits. The inverse mapping recovers (x, y, t) by successive division and modulus operations.

### 2.3 Angular Distance

The angular distance between two heading bins a and b is defined as min(|a − b|, Nθ − |a − b|), which correctly handles wraparound. The goal is considered reached when the cell matches exactly and the angular distance is at most Gθ = 2 bins (i.e., ±10°).

## 3. Obstacle Inflation via Exact EDT

### 3.1 Purpose

The planner inflates obstacles by a configurable radius r (in cell units) so that the vehicle's footprint can be approximated as a point during search. A cell in the inflated grid is marked occupied if and only if its Euclidean distance to the nearest occupied cell in the original grid is at most r.

### 3.2 Felzenszwalb–Huttenlocher Algorithm

The exact EDT is computed in two separable passes using the parabolic-envelope technique of Felzenszwalb and Huttenlocher:

1. **Column pass.** For each column x, construct a 1D function f(y) where f(y) = 0 if cell (x, y) is occupied and f(y) = +∞ otherwise. Compute the 1D squared-distance transform along the column, yielding an intermediate array D₁(x, y) storing the minimum squared vertical distance to any obstacle in that column.

2. **Row pass.** For each row y, treat D₁(x, y) as the input function and compute another 1D squared-distance transform along the row. The result D(x, y) is the exact squared Euclidean distance to the nearest obstacle.

The 1D squared-distance transform operates in O(n) time using a lower-envelope construction over parabolas. It maintains a stack of parabola vertices and intersection points. For each query point q, the algorithm identifies which parabola in the envelope achieves the minimum at q by scanning the precomputed intersection array. The overall 2D transform runs in O(W · H) time and O(W · H) additional memory.

### 3.3 Inflation Thresholding

After computing the full squared-distance field D(x, y), the inflated occupancy grid is produced by:

inflated(x, y) = 1 if D(x, y) ≤ r², else 0

If the inflation radius is zero or negative, the inflated grid is a direct copy of the input occupancy grid.

## 4. Motion Primitives

### 4.1 Steering Actions

From each heading bin t, the planner generates N_a = 5 motion primitives corresponding to heading-bin changes of Δt ∈ {−2, −1, 0, +1, +2}. Each primitive has a fixed arc length L = 1.45 cells, chosen so that the resulting cell displacement typically lands in a neighboring grid cell.

### 4.2 Endpoint Computation

**Straight (Δt = 0):** The endpoint is simply:

(Δx, Δy) = (L·cos θ₀, L·sin θ₀)

**Turning (Δt ≠ 0):** The heading change is Δθ = 2π · Δt / Nθ and the curvature is κ = Δθ / L. The endpoint of the constant-curvature arc is:

Δx = [sin(θ₀ + Δθ) − sin(θ₀)] / κ

Δy = −[cos(θ₀ + Δθ) − cos(θ₀)] / κ

The continuous endpoint is rounded to the nearest integer cell. If rounding yields (Δx, Δy) = (0, 0) (no cell progress), a fallback forces movement by one cell in the dominant direction.

### 4.3 Cost Assignment

The cost of a straight primitive is L = 1.45. Turning primitives are penalized by a factor of α = 1.05, yielding cost α · L = 1.5225. This biases the planner toward straighter paths.

### 4.4 Collision Footprint

For collision checking, each primitive stores a list of grid cells that the arc sweeps through. These are computed by sampling the arc at N_s = 10 evenly spaced points along the arc length and connecting consecutive samples with Bresenham line rasterization. The resulting cell offsets (relative to the source cell) are deduplicated and stored in the primitive, capped at 32 offsets.

During search, a primitive is rejected if any cell in its footprint (excluding the source cell itself) lies outside the grid or is marked occupied in the inflated grid.

## 5. Heuristic Function

### 5.1 2D Backward Dijkstra

A 2D heuristic h(x, y) is precomputed by running Dijkstra's algorithm backward from the goal cell on the inflated 2D grid with 8-connected movement. Cardinal moves have cost 1.0 and diagonal moves have cost √2 ≈ 1.414. This produces the true shortest 2D path cost from every reachable cell to the goal, ignoring heading constraints.

### 5.2 Admissibility

Because the 2D heuristic ignores the heading dimension and uses unit-cost moves that underestimate the lattice primitive cost (L = 1.45 > 1.0), h(x, y) never overestimates the true cost-to-go. It is therefore admissible. However, it is not the tightest possible bound; a non-holonomic heuristic (e.g., Reeds-Shepp) could be added as a secondary heuristic and the maximum taken.

### 5.3 Unreachability Detection

If h(x_s, y_s) = ∞ after the Dijkstra flood, the start is not 2D-reachable from the goal and the planner returns failure immediately. Similarly, during search, any successor whose 2D heuristic is infinite is pruned.

## 6. Hybrid A* Search

### 6.1 Data Structures

The search maintains the following arrays, all indexed by the packed state integer:

- **g(s):** The cost of the best known path from the start to state s. Initialized to infinity for all states except the start state, which is set to zero.

- **parent(s):** The predecessor state on the best known path. Initialized to a sentinel value (UINT32_MAX) indicating no parent.

- **closed(s):** A boolean flag indicating whether state s has been expanded. Initialized to false for all states.

A binary min-heap (priority queue) ordered by f = g + h stores the open set. The heap supports push and pop in O(log n) time and grows dynamically by doubling its capacity.

### 6.2 Main Loop

The algorithm proceeds as follows:

1. Initialize: set g(start) = 0, push start onto the open heap with key f = h(x_s, y_s).

2. Pop the state s with the smallest f-value from the heap.

3. If s has already been closed, skip it (stale entry). Also skip if the popped key exceeds g(s) + h(s) by more than a small epsilon, which detects duplicate insertions from later-improved g-values.

4. Test goal condition: if s matches the goal cell and the angular distance is within the tolerance, terminate with success.

5. Mark s as closed.

6. For each of the 5 steering actions, compute the successor state. If the successor is out of bounds, collides with the inflated grid, or has an infinite 2D heuristic, skip it.

7. If the new g-cost to the successor is strictly better (by more than epsilon) than its current g-cost, update g, set the parent pointer, reopen the state if it was previously closed, and push it onto the heap with key g + h.

8. Repeat from step 2 until the goal is found or the heap is empty (failure).

### 6.3 Reopening Closed States

Notably, the algorithm allows reopening of closed states: if a shorter path to an already-expanded state is discovered, its closed flag is cleared and it is re-inserted into the heap. This can occur because the heuristic, while admissible, is not perfectly consistent in the 3D state space (since it ignores heading). This ensures optimality with respect to the discretization at the cost of potentially re-expanding some states.

## 7. Path Reconstruction

Once the goal state is identified, the path is reconstructed by following parent pointers backward from the goal to the start. Each state is unpacked into a pose (x, y, θ) where x and y are the cell coordinates (as floats) and θ is the continuous angle corresponding to the heading bin. The resulting list is reversed to yield a start-to-goal sequence.

The output path has one pose per expanded state along the optimal route. Intermediate arc waypoints within each primitive step are not included in the output; the caller may interpolate between consecutive poses using the known primitive geometry if smoother trajectories are needed.

## 8. Summary of Parameters

| Parameter | Value | Description |
|---|---|---|
| Nθ | 72 | Number of heading bins (5° resolution) |
| N_a | 5 | Steering actions per heading bin |
| Δt range | {-2..+2} | Heading bin deltas for the 5 actions |
| L (step length) | 1.45 | Arc length of each primitive in cells |
| α (turn penalty) | 1.05 | Cost multiplier for non-straight primitives |
| Gθ (goal tol.) | 2 bins | Maximum heading-bin difference for goal match |
| N_s (coll. samples) | 10 | Points sampled along each arc for collision checking |
| Max offsets | 32 | Maximum collision-check cells per primitive |
| 2D connectivity | 8 | 8-connected grid for heuristic Dijkstra |

## 9. Computational Complexity

### 9.1 Preprocessing

- EDT inflation: O(W · H) time and space.

- Primitive construction: O(Nθ · N_a · N_s), a small constant (72 × 5 × 10 = 3600 iterations).

- 2D Dijkstra heuristic: O(W · H · log(W · H)) time using a binary heap.

### 9.2 Search

In the worst case, every state may be expanded, giving O(W · H · Nθ · N_a · log(W · H · Nθ)) time. In practice, the heuristic focuses expansion toward the goal and the number of expanded states is a small fraction of the total. Memory usage is dominated by the g-cost, parent, and closed arrays, totaling approximately 9 · W · H · Nθ bytes (4 + 4 + 1 per state).

## 10. Correctness Properties

### 10.1 Soundness

Every path returned by the algorithm satisfies the following invariants: (a) each consecutive pair of poses is connected by a valid motion primitive, (b) no primitive footprint intersects an inflated obstacle, (c) all poses lie within grid bounds, and (d) the first pose is the start and the last pose satisfies the goal condition.

### 10.2 Completeness

The planner is resolution-complete with respect to the discretization: if a path exists through the lattice graph (the set of all states reachable by chaining primitives from the start), the algorithm will find it. This follows from the exhaustive nature of the A* search with an admissible heuristic and the reopening mechanism. However, the planner is not complete in the continuous sense; a feasible continuous path may exist that has no lattice analog due to discretization.

### 10.3 Optimality

Within the lattice graph, the returned path is optimal (minimum cost) under the defined cost model. The admissibility of the heuristic ensures that no state is prematurely finalized with a suboptimal cost. The reopening of closed states compensates for the heuristic's inconsistency in the heading dimension.

### 10.4 Termination

The algorithm is guaranteed to terminate. Each state can be inserted into the heap at most once per improvement of its g-cost, and since costs are positive and bounded, the number of improvements per state is finite. If no path exists, the heap will eventually empty and the planner reports failure.

## 11. Failure Modes and Edge Cases

The planner returns failure (path length −1) in any of the following situations:

- Invalid input: null pointers, non-positive dimensions, or a state count exceeding 32-bit capacity.

- Start or goal is out of bounds or lies within the inflated obstacle region.

- The 2D heuristic indicates the start is unreachable from the goal (disconnected regions).

- The open heap is exhausted without reaching the goal (no lattice path exists).

- The reconstructed path exceeds the caller-provided buffer size.

- Memory allocation failure at any stage.

## 12. Implementation Notes for Code Generation

An implementation of this specification should observe the following details:

- The EDT must use double-precision arithmetic internally to avoid overflow on large grids, even though the final thresholding is a simple comparison.

- The heap should use lazy deletion: duplicate state entries are tolerated and filtered at pop time by checking against the current g-cost. This avoids the need for a decrease-key operation.

- Epsilon comparisons (with ε = 10⁻⁶) are used for g-cost updates and stale-entry detection to avoid floating-point churn.

- The primitive collision check skips the source cell (offset (0,0)) because the source cell's validity was already established when the source state was expanded.

- Bresenham line rasterization is used between consecutive arc samples to avoid missing cells at coarse sampling rates.

- The finite "infinity" sentinel (10³⁰) must be large enough that no real accumulated cost could approach it, but not so large as to cause floating-point overflow when added to other costs.

- All grid coordinate bounds checks use unsigned comparison tricks (casting to unsigned and comparing against the dimension) to handle negative coordinates in a single branch.
