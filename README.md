# eugene-plexus-memory

> **Retired as of 2026-09-08.** Historical conversation storage from the former
> consciousness framework; not part of the current inference control plane.
> The documentation below is historical, not a current installation guide.
> Its contracts were removed from current specs; retain the historical pin.
> The repository remains unarchived on GitHub, but is not maintained as an active
> component. See the [current project overview](https://github.com/eugene-plexus/specs#readme).

Conversation history storage for [Eugene Plexus](https://github.com/eugene-plexus).

## What this is

The memory component of Eugene Plexus. v0.1 implements the minimum surface
the orchestrator needs to function — append-and-fetch by conversation id —
backed by an in-process dict. The HTTP shape is the long-term contract;
the storage backend will grow into something durable in v0.2+.

```
POST   /v1/conversations                       create empty conversation
GET    /v1/conversations/{id}                  fetch full message history
DELETE /v1/conversations/{id}                  drop a conversation
POST   /v1/conversations/{id}/messages         append a message
```

Plus the standard Eugene Plexus config trio (`GET /v1/config`,
`GET /v1/config/schema`, `PATCH /v1/config`) and `GET /healthz`.

## Why memory is built last

Per `CLAUDE.md` in the planning workspace: memory is built fifth in the
v0.1 build order so its interface is shaped by the real usage patterns
of the orchestrator and UI rather than speculation. v0.1's in-memory
stub is deliberately minimal — vector retrieval, semantic recall,
sleep-consolidation hooks, and episodic-vs-semantic tiering are all
deferred.

## Quick start

```bash
pip install -e ".[dev]"
python -m eugene_plexus_memory
# default port 8083; override via PATCH /v1/config or the config file
```

The first run creates a `config.yaml` in the working directory with the
component's defaults. Edit through the UI, through `PATCH /v1/config`, or
by hand.

## Degraded-mode startup

Per the project-wide rule (`feedback_degraded_mode_required.md`), a bad
config never prevents the component from starting. Config endpoints stay
reachable so operators can fix the broken setting through the UI;
domain endpoints return `503` with an actionable `Problem` body until
the config is fixed.

For v0.1's in-process store there's nothing that can actually fail at
init — the lifespan still wires up the pattern so future persistence
backends can fail gracefully without touching the routing layer.

## Codegen

Pydantic models for the memory and shared schemas are generated from
the pinned `eugene-plexus/specs` commit:

```bash
python scripts/codegen.py
```

`SPECS_REF` records the commit SHA. Bump it to track a newer specs
release; CI re-runs codegen and fails if the working tree drifts.

## License

Apache-2.0. See [`LICENSE`](LICENSE) and
[`CONTRIBUTING.md`](CONTRIBUTING.md) (DCO sign-off required).
