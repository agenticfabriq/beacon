"""Beacon background workers.

Four independent processes:

  beacon-worker-promotion    - hourly: extract candidate eval items from traces
  beacon-worker-convergence  - continuous: auto-promote on SUT agreement
  beacon-worker-antigoodhart - continuous: scan registry for leakage / skew
  beacon-worker-retention    - daily: enforce 90-day full-trace retention

Each worker runs in its own process. No shared in-memory state; all
coordination is through Postgres and object storage.
"""

__version__ = "0.2.0"
