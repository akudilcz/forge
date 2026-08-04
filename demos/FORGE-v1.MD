# Hybrid A* Path Planner — System Whitepaper

## 1. Overview

This system implements a Hybrid A* search algorithm for motion planning over a continuous 2D grid. Unlike classical A*, Hybrid A* tracks the continuous heading of the vehicle so that planned paths are kinematically feasible for car-like robots and autonomous vehicles.

## 2. Functional Requirements

### 2.1 Path Planning

- The system shall accept a start pose (x, y, heading) and a goal pose (x, y, heading) and return a smooth, kinematically-feasible path connecting them.

- The system shall respect a configurable minimum turn radius parameter that models the maximum curvature constraint of the physical vehicle.

- The planner shall produce a path that avoids all occupied cells in the provided occupancy grid map.

- The system shall return the path as an ordered sequence of (x, y, heading) waypoints at a configurable resolution.

### 2.2 Obstacle Avoidance

- The planner shall treat any grid cell with occupancy value above a configurable threshold as an impassable obstacle.

- The system shall support an optional inflation radius that expands obstacle boundaries to provide a configurable safety margin around obstacles.

### 2.3 Performance

- The system shall return a solution path within a configurable timeout.

- If no path is found within the timeout, the system shall return a failure indication with a descriptive reason.

## 3. Non-Functional Requirements

- The system shall be implemented as a Python library with a public API that is importable without side effects.

- All public functions shall include type annotations compatible with Python 3.11 or later.

- The system shall provide unit tests achieving at least 90% line coverage of the core planning algorithm.
