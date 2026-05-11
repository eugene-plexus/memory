"""Runtime configuration: schema declaration + file-backed state + PATCH apply.

Implements the shared Eugene Plexus config protocol:

* `GET /v1/config/schema` -> field metadata for UI rendering (`as_schema()`)
* `GET /v1/config` -> current effective values, secrets redacted (`as_document()`)
* `PATCH /v1/config` -> partial update, per-key validation (`apply_patch()`)

Storage backend in v0.1 is a flat YAML file. Sensitive values are stored
plain on disk for now; at-rest encryption is future work.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from ._generated.models import (
    ConfigDocument,
    ConfigField,
    ConfigFieldError,
    ConfigFieldShowWhen,
    ConfigSchema,
    ConfigUpdateRequest,
    ConfigUpdateResult,
    ConfigValueType,
)

REDACTED = "<redacted>"

CATEGORY_LABELS: dict[str, str] = {
    "backend": "Storage Backend",
    "embeddings": "Embeddings",
    "network": "Network",
    "logging": "Logging",
    "limits": "Limits",
}

# Schema for the memory component's config surface. v0.1 is intentionally
# minimal — when the in-process store is replaced with real persistence,
# this is where DB connection strings, retention windows, etc. land.
# `port` used to live in this list. It moved out: ports are owned by
# the watchdog topology now, passed to spawned children via
# EUGENE_PLEXUS_MEM_BIND_PORT. Standalone launches fall back to the
# default in `__main__.py`.
FIELDS: list[ConfigField] = [
    ConfigField(
        key="backend",
        label="Storage backend",
        description=(
            "Which storage backend the memory component uses. "
            "`local_sqlite` (the v0.2 default) stores conversations in a "
            "SQLite file on disk and supports per-person retrieval. "
            "`in_process` is the v0.1 in-memory store — useful for tests "
            "or transient dev runs but contents are lost on restart. "
            "Future versions add `mem0`, `holographic`, `obsidian` and "
            "others as drop-in adapters."
        ),
        category="backend",
        valueType=ConfigValueType.enum,
        default="local_sqlite",
        enumValues=["local_sqlite", "in_process"],
        requiresRestart=True,
    ),
    ConfigField(
        key="localSqlitePath",
        label="SQLite database path",
        description=(
            "Filesystem path of the `local_sqlite` backend's database "
            "file. Defaults to `memory.sqlite3` next to the memory "
            "component's config file. Use an absolute path if you want "
            "the database somewhere else; the parent directory is "
            "created automatically. Only consulted when "
            "`backend: local_sqlite`."
        ),
        category="backend",
        valueType=ConfigValueType.file_path,
        default="memory.sqlite3",
        requiresRestart=True,
        showWhen=ConfigFieldShowWhen(key="backend", equals="local_sqlite"),
    ),
    ConfigField(
        key="embeddingSource",
        label="Embedding source",
        description=(
            "Where embeddings come from when memory search is wired. "
            "`local` runs sentence-transformers offline (~100MB model "
            "download on first install). `api` calls a vendor (OpenAI / "
            "Voyage / Cohere) — requires network and a vendor key. "
            "Reserved for the v0.2.x follow-on that wires `POST "
            "/v1/memory/search`; in this build the field is recorded "
            "but search returns 503 regardless."
        ),
        category="embeddings",
        valueType=ConfigValueType.enum,
        default="local",
        enumValues=["local", "api"],
        requiresRestart=True,
        showWhen=ConfigFieldShowWhen(key="backend", equals="local_sqlite"),
    ),
    ConfigField(
        key="logLevel",
        label="Log level",
        description=(
            "How chatty the memory service's terminal output is. "
            "`DEBUG` prints every store/fetch operation; `INFO` is the "
            "normal level; `WARNING` and `ERROR` go progressively "
            "quieter."
        ),
        category="logging",
        valueType=ConfigValueType.enum,
        default="INFO",
        enumValues=["DEBUG", "INFO", "WARNING", "ERROR"],
        requiresRestart=True,
    ),
    ConfigField(
        key="maxConversations",
        label="Max conversations",
        description=(
            "Upper limit on how many conversations the v0.1 in-process "
            "store will keep at once. When exceeded, the oldest "
            "conversation is dropped to make room. Acts as a safety "
            "valve against unbounded RAM growth — pick something "
            "comfortably above how many you expect to keep around."
        ),
        category="limits",
        valueType=ConfigValueType.integer,
        default=1000,
        minimum=1,
    ),
    ConfigField(
        key="maxMessagesPerConversation",
        label="Max messages per conversation",
        description=(
            "Upper limit on messages within a single conversation. "
            "When exceeded, the oldest messages in that conversation "
            "are dropped on append (the user's first turns disappear "
            "first). 10,000 is roughly the size of a long working "
            "session; raise it for genuinely long-running conversations."
        ),
        category="limits",
        valueType=ConfigValueType.integer,
        default=10000,
        minimum=1,
    ),
]

_FIELDS_BY_KEY: dict[str, ConfigField] = {f.key: f for f in FIELDS}


def as_schema() -> ConfigSchema:
    return ConfigSchema(
        component="memory",
        fields=FIELDS,
        categories=CATEGORY_LABELS,
    )


def _defaults() -> dict[str, Any]:
    return {f.key: f.default for f in FIELDS if f.default is not None}


def _validate_value(field: ConfigField, value: Any) -> str | None:
    """Return None if valid, otherwise an error message."""
    if value is None:
        return None  # null clears to default

    vt = field.valueType

    if vt == ConfigValueType.string or vt == ConfigValueType.url or vt == ConfigValueType.file_path:
        if not isinstance(value, str):
            return f"expected string, got {type(value).__name__}"
        if field.pattern is not None:
            import re

            if re.search(field.pattern, value) is None:
                return f"value does not match pattern {field.pattern!r}"
        return None

    if vt == ConfigValueType.secret:
        if not isinstance(value, str):
            return f"expected string, got {type(value).__name__}"
        if value == REDACTED:
            return "refusing to write the literal redacted value back"
        return None

    if vt == ConfigValueType.integer:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"expected integer, got {type(value).__name__}"
        if field.minimum is not None and value < field.minimum:
            return f"must be >= {field.minimum}"
        if field.maximum is not None and value > field.maximum:
            return f"must be <= {field.maximum}"
        return None

    if vt == ConfigValueType.number or vt == ConfigValueType.duration:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"expected number, got {type(value).__name__}"
        if field.minimum is not None and value < field.minimum:
            return f"must be >= {field.minimum}"
        if field.maximum is not None and value > field.maximum:
            return f"must be <= {field.maximum}"
        return None

    if vt == ConfigValueType.boolean:
        if not isinstance(value, bool):
            return f"expected boolean, got {type(value).__name__}"
        return None

    if vt == ConfigValueType.enum:
        if not isinstance(value, str):
            return f"expected string, got {type(value).__name__}"
        allowed = field.enumValues or []
        if value not in allowed:
            return f"must be one of {allowed}"
        return None

    return f"unsupported valueType: {vt}"


class ConfigStore:
    """File-backed config state. Thread-safe for the simple read/write pattern."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._values: dict[str, Any] = _defaults()
        self._pending_restart: set[str] = set()

    def load(self) -> None:
        """Load from the configured file, creating it with defaults if absent."""
        with self._lock:
            if self._path.exists():
                raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ValueError(f"config file {self._path} must be a YAML mapping at the root")
                merged = _defaults()
                for k, v in raw.items():
                    if k in _FIELDS_BY_KEY:
                        merged[k] = v
                self._values = merged
            else:
                self._values = _defaults()
                self._write_locked()

    def as_document(self) -> ConfigDocument:
        with self._lock:
            out: dict[str, Any] = {}
            for key, value in self._values.items():
                field = _FIELDS_BY_KEY.get(key)
                if field is not None and field.sensitive and value is not None:
                    out[key] = REDACTED
                else:
                    out[key] = value
            return ConfigDocument.model_validate(out)

    def apply_patch(self, request: ConfigUpdateRequest) -> ConfigUpdateResult:
        applied: list[str] = []
        rejected: list[ConfigFieldError] = []
        pending_restart: list[str] = []

        # ConfigUpdateRequest is a free-form mapping; iterate its raw dict form.
        patch: dict[str, Any] = request.model_dump()

        with self._lock:
            for key, new_value in patch.items():
                field = _FIELDS_BY_KEY.get(key)
                if field is None:
                    rejected.append(ConfigFieldError(key=key, message="unknown field"))
                    continue

                err = _validate_value(field, new_value)
                if err is not None:
                    rejected.append(ConfigFieldError(key=key, message=err))
                    continue

                if new_value is None and field.default is not None:
                    self._values[key] = field.default
                else:
                    self._values[key] = new_value

                applied.append(key)
                if field.requiresRestart:
                    self._pending_restart.add(key)
                    pending_restart.append(key)

            if applied:
                self._write_locked()

            requires_restart = bool(self._pending_restart)
            return ConfigUpdateResult(
                applied=applied,
                rejected=rejected,
                requiresRestart=requires_restart,
                pendingRestart=sorted(self._pending_restart),
            )

    def get(self, key: str) -> Any:
        with self._lock:
            return self._values.get(key)

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(self._values, f, sort_keys=True, default_flow_style=False)
