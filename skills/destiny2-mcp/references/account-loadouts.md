# Account loadouts

`loadout_assistant(intent="list"/"get")` reads account-saved loadouts only: local snapshots plus Bungie official slots. It is not a community search.

Each returned item uses the shared `build_template` shape so it can be compared with community records. That template is descriptive; it is not a `canonical_build`.

Official Bungie slots additionally expose execution/display metadata such as `slot_number`, `native_character_id`, and name/icon/color hashes. Current accounts may expose 20 slots per character. Slot numbers are user-facing 1–20; do not assume the old 10-slot limit or rely on an absent `loadoutIndex`.

When presenting an account loadout, distinguish `source="local"` from `source="bungie"`, descriptive template fields from executable native slot IDs, and a saved loadout from a community recommendation.

Saving, deleting, equipping, snapshotting, updating identifiers, or clearing a slot are writes. Read the current target first and wait for explicit confirmation.
