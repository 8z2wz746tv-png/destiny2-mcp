---
name: destiny2-render
description: Use when a Destiny 2 MCP result should be shown to the player as cards instead of raw JSON - weapon and armor cards, perk rows, activity and raid report rows, build candidates, inventory and pattern lists. Maps every rendered field to the tool and intent that returns it, fixes the HTML subset and inline styles that survive a chat host, and states what must not be rendered when a field is missing.
metadata:
  short-description: Render Destiny 2 MCP results as HTML cards
---

# Destiny 2 MCP → rendered cards

This skill turns the JSON of the local Destiny 2 MCP into blocks a player can read:
a weapon card with icons and perk rows, an armor card, activity and raid-report
rows, build candidates, inventory/pattern lists. It is a presentation layer only —
the tool result stays the source of truth, and nothing here adds data.

Read it when the answer is worth a card. Skip it for one-line answers: rendering a
whole card for "you own four copies of one hand cannon" is noise.

## 1. Check the host before writing any HTML

| Host capability | What to emit |
| --- | --- |
| Renders HTML in the transcript (Doubao's chat renderer, a web client, a preview pane) | the HTML blocks in `references/blocks.md`, exactly as specified |
| Renders Markdown only | the same blocks as Markdown: a heading line, a table, `-` rows. Keep every field, drop the icons (write the name; do not paste an image tag) |
| Plain text only | the same rows as text lines, one per item |

Never assume one host. Never emit `<style>`, a `<link>` to a stylesheet, a script,
or an external font — a host that does render HTML still may strip them. All
styling is inline on the element that needs it; a block that loses its styling must
still be readable.

## 2. The three data tiers must stay apart on the card

- **Manifest** (`perk_pool`, `info`, `catalog`, `god_roll`, `armor_mods`, `set_bonus`):
  what the game defines. Label it as "可能/可滚出", never as "你有".
- **Account** (`analyze`, `compare`, `filter_rolls`, `type`, `patterns`, `inventory`,
  `loadouts`, `activity`, `build`, `subclass`): what this player has or did.
- **Community** (any `community` intent, `farming`, `starside`, `popularity`): what
  other people say. Always carry its own source line (`source_ref` / `source.label` /
  `attribution` / `updated_at` / `trust`) and never restyle it as official fact.

A card that mixes two tiers must say which line is which. The full evidence rules
live in the `destiny2-mcp` skill; the rendering rule is only: **print the provenance
that the response gave you, or leave the claim off the card.**

## 3. Never invent a field, never invent an icon

1. Every field name used on a card must exist in the response path listed in
   `references/blocks.md`. If a path is not in that table, it is not rendered —
   no approximations, no "close enough" key.
2. `icon_url` is the only image key. When it is an empty string, render a sized
   placeholder tile, not `<img src="">`.
3. Never build an icon URL yourself. The file name is **not** the item hash
   (`hash → definition → displayProperties.icon`), so a hand-made URL is a broken
   image with a plausible look.
4. `null`, `""`, `false` and "not fetched" are four different things. `null` is not
   `0`, an empty perk list is not "no perks", and `unavailable` is not "none".
   `references/blocks.md` lists the per-field wording for these cases.

## 4. Every card carries its scope

Rows that come from a window, a page, a scan or one account tier must print that
context on the card — the window line of `pvp_weapons`, `覆盖 / 分页` for a
truncated list, `perk_data_complete` for duplicates, `not_scanned` for raid rows.
A number without its window is a wrong number. The exact fields are in
`references/html-conventions.md` §5.

## 5. Reference files

- [blocks.md](references/blocks.md) — block index (which intent feeds which block),
  the verified field table per block, the HTML skeleton, and the degradation rule
  for every optional field.
- [html-conventions.md](references/html-conventions.md) — allowed tags, the inline
  style subset, icon sizes, the escaping rule, layout baseline, and how to mount the
  archive-relative community images.

`references/blocks.md` is machine-checked against live responses by
`scripts/verify_render_fields.py` (repo-side): every path in its tables is resolved
against a real call to the named intent. If a field is renamed, that script fails
before a model can render a blank card.
