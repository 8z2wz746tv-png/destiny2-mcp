"""Build Engine — Destiny 2 armor build solving and optimization.

Build Engine is the project's first domain engine module. It is a pure
computation layer: no I/O, no Bungie API calls, no MCP dependencies.

Architecture:
    server.py / CLI / REST
          ↓
    BuildService (services/build_service.py)
          ↓
    BuildEngine
    ├── ConstraintParser  (constraints.py)
    ├── Solver            (solver.py)     — Branch and Bound
    ├── Scorer            (scorer.py)     — multi-objective ranking
    └── Analyzer          (analyzer.py)   — failure explanation

Phase 0 (current): data models + InventorySnapshot constructor.
Phase 1: core solver.
Phase 2: stat mods + masterwork support.
"""
