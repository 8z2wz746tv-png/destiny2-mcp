# ADR-007: Canonical build strategy

- Status: accepted
- Scope: `destiny_mcp/build_contracts.py`, `services/build_service.py`, `tools/_build_confirmation.py`

This decision was implemented before it was written down; the code references it from
`build_import/`, so it is recorded here retroactively.

## Context

Builds reach this project from several directions: the armor solver, importer output parsed
from articles or screenshots, and community templates. They differ in what they actually prove.

A list of item names is not an executable plan. Names can be ambiguous, several items can
share a name, perks are per-instance, and an armor plan is only meaningful against a specific
inventory snapshot. Treating a name-level build as permission to write to the account would
let a typo or a stale template move real items.

## Decision

Three types, each a strict superset of the previous one, and only the last one may be executed.

**`BuildRecipe`** — the internal representation. Bungie definition hashes only, no instance
IDs and no execution fields at all. It is what the importer and the solver speak.

**`CanonicalBuild(BuildRecipe)`** — the backward-compatible wire format. It adds
`subclass_instance_id`, `subclass_plug_sockets`, `items`, `snapshot_version` and
`execution_id`, but every one of those is optional. Its docstring says so explicitly: it is
*not* sufficient proof of executability. It exists so older callers keep working.

**`ExecutableBuild(CanonicalBuild)`** — the only type the executor accepts. It enforces, at
validation time:

- `class_type` is one of `hunter` / `warlock` / `titan`
- exactly five `items`, with five unique non-empty `item_instance_id` values
- exactly one item per armor slot (`helmet`, `gauntlets`, `chest`, `legs`, `class_item`)
- every mod hash is positive
- `snapshot_version` and `execution_id` are non-empty

So a `BuildRecipe` cannot be validated as an `ExecutableBuild`; the extra fields are not
derivable from it. That is intentional, and there is a test asserting it.

## Candidate binding

`execution_id` is a one-time credential issued by the server, not by the caller.
`BuildService` keeps issued candidates in an `OrderedDict` with an issue time and the player
they were issued to, evicts them on a TTL, and validates an incoming plan against that store.
A candidate that has expired, was never issued, or belongs to another player is rejected.

The same idea covers exotic selection: `tools/_build_confirmation.py` issues an HMAC-signed,
time-limited token keyed on the client secret, and `build_assistant` requires the exact
candidate arguments back before it will continue.

## Consequences

- A community template, a `solver_handoff`, or a `build_template` can never be passed to
  `equip_build`; they are not `CanonicalBuild`s and carry no server-issued `execution_id`.
- Editing a candidate by hand does not help: the store holds the server's own copy and
  compares against it.
- Changing armor between generating and executing a candidate invalidates it, because the
  snapshot no longer matches. The tools are expected to report that and ask for a re-solve
  rather than silently substituting another candidate.
- The cost is that every new executable capability has to be expressed in `ExecutableBuild`
  and validated in `BuildService`, instead of being passed through as free-form JSON.
