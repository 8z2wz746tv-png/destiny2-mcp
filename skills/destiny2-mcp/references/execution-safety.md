# Execution safety

The user asking to inspect, explain, compare, recommend, or test does not authorize a write.

## Required write sequence

1. Read the current state with the appropriate account-aware intent.
2. Resolve an exact target: item instance, character, loadout ID, official slot, or server-issued `canonical_build`.
3. Show the action, target, source and affected character/slot.
4. Wait for an unambiguous user confirmation.
5. Send the same target with `confirmed=true`.
6. Report the actual result, including partial failures, then re-read state when practical.

This applies to inventory move/transfer/equip/lock/track actions, `build_assistant(intent="equip_build")`, loadout save/delete/equip and official-slot writes, and subclass or artifact modifications.

## Build execution

Only a candidate returned by the build solver with a complete `canonical_build` can enter `equip_build`. Validate and preserve exact instance IDs, armor slots, mod hashes, subclass sockets, snapshot version, and execution ID. Never rebuild it from a score or from a descriptive template.

If the candidate is stale, validation fails, or the inventory changed, stop and generate a new candidate. Do not retry a failed write blindly.

## No hidden writes

A test must remain read-only unless the user explicitly asks to test a write and then confirms the exact operation. Never set `confirmed=true` on the agent’s own initiative.
