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

### Requirement sourcing

Every requirement row carries a `sourcing` slot. It answers "where do I get this", and it is deliberately separate from `required_perks`.

- `available=true` — a local list covers this item. `source` is where the list says to get it, plus whatever the list also records (`tier`, `scenario`, `pieces`, `note`, `scale`, `rank`). `recommended_perks` is the list author's per-slot candidates and uses `recommended_perk_match="any_within_column"`: one perk in a column is enough. That is **not** the same as the template's `required_perks`, which are the perks the build asks for and must all be present. Never merge the two or report one as the other.
- `available=false` with `reason="not_listed"` — the list is installed but does not cover this item. This is not a verdict that it cannot be farmed.
- `available=false` with `reason="list_unavailable"` — no list data is installed.
- `available=false` with `reason="no_adapter"` — this category has no sourcing data source yet. Report it as not integrated; never as "no source" or "unfarmable".

Weapon and armor-set sourcing are wired up; exotic armor, armor mods, artifact and artifact mods keep the slot but are not integrated. `source` comes from a community snapshot, not from Bungie, so it is not live drop data. Every `source` value may be absent, and armor templates frequently name a set after its activity (for example 玻璃拱顶) while both the Manifest and the lists use the set name (埃希恩记忆) — an unmatched armor name is `unknown_definition` or `not_listed`, not a missing item.

## Execution boundary

To equip a community idea, convert confirmed requirements through the normal build solver. Generate a server-issued, instance-bound `canonical_build`, show the exact items and changes, and obtain confirmation. Never pass a community template, `solver_handoff`, or a Starside title to `equip_build`.
