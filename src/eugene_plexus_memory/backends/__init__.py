"""Memory storage backends.

The memory component's HTTP surface is the long-term contract; the
storage backend is pluggable. v0.2 ships:

  - `in_process` (v0.1 in-memory store, retained for tests / standalone)
  - `local_sqlite` (v0.2 default — SQLite on disk, person-keyed retrieval)

Future versions add `mem0`, `holographic` (HRR), `hindsight` (graph),
`obsidian` (vault), etc. as drop-in adapters. The registry-based shape
keeps the wire contract stable across additions.
"""

from .base import (
    NIL_PERSON_ID,
    Backend,
    BackendError,
    PersonRecentResult,
    build_backend,
)

__all__ = [
    "NIL_PERSON_ID",
    "Backend",
    "BackendError",
    "PersonRecentResult",
    "build_backend",
]
