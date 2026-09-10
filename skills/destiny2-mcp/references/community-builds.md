# Community builds and Starside

Starside is a local community data layer inside the existing domain tools. The bundled author Markdown provides reference material; an optional schema v2 archive adds complete community build templates. Neither source is a ninth tool, Bungie data, or live web search.

Do not turn weapon recommendation rows, subclass references, or armor-set documents into invented character builds. If `build_count=0`, report that complete community build templates are not installed and offer the available reference queries instead.

## Search then select

For a list:

```text
build_assistant(intent="community", character="hunter", top_n=5, include_inventory=false)
```

Use the returned `community_build_id` for a complete template. Do not invent an ID from a title. If there is more than one result, do not read account inventory until the user selects a template or explicitly provides its ID.

## Template semantics

Preserve `format_version`, `source`, `class`, `weapons`, `armor`, `artifact`, `stat_targets`, `notes`, `unparsed`, and uncertainty markers. `source.content_scope="community_build_template"` and `executable=false` are important evidence boundaries.

The template can describe weapons, Perks, armor, mods, subclass, artifact, stat ranges, sets, scenarios, and source pages. Missing fields remain missing. A `solver_handoff` is only partial solver input and is not a complete executable plan.

## Inventory matching

Only after a concrete template is selected:

```text
build_assistant(
  intent="community",
  community_build_id="<returned id>",
  include_inventory=true
)
```

Report held, missing, unknown, not-read, and unverified requirements separately. A match does not authorize equipping. It also does not prove that all weapon, skill, mod, artifact, energy, slot, or stat constraints are executable.

## Execution boundary

To equip a community idea, convert confirmed requirements through the normal build solver. Generate a server-issued, instance-bound `canonical_build`, show the exact items and changes, and obtain confirmation. Never pass a community template, `solver_handoff`, or a Starside title to `equip_build`.
