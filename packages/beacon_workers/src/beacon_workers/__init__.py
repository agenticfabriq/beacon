"""Beacon background workers.

One process:

  beacon-worker-retention    - daily: enforce 90-day full-trace retention

Each worker runs in its own process. No shared in-memory state; all
coordination is through Postgres and object storage.
"""

__version__ = "0.2.0"
