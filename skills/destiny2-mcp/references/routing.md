# Tool routing

The normal profile exposes eight aggregate tools. Use the exact tool and intent below; do not invent legacy tool names.

| User request | Tool and intent |
| --- | --- |
| Player profile or characters | `player_assistant(intent="profile")` |
| Inventory summary or a specific account item | `inventory_assistant(intent="summary"/"get"/"search"/"type")` |
| Account weapons with a current Perk | `weapon_assistant(intent="filter_rolls", include_inventory=true)` |
| All Manifest weapons with a Perk | `weapon_assistant(intent="catalog")` |
| Current instance comparison | `weapon_assistant(intent="compare")` |
| Possible Perk pool | `weapon_assistant(intent="perk_pool")` |
| Perk definition/effect | `weapon_assistant(intent="perk_description")` |
| Community weapon/Perk reference | `weapon_assistant(intent="community")` |
| Armor recommendation or diagnosis | `build_assistant(intent="recommend"/"find"/"analyze")` |
| Future armor farming target | `build_assistant(intent="farm_target")` |
| Community build template | `build_assistant(intent="community", include_inventory=false)` |
| Community template inventory match | Repeat `build_assistant(intent="community", community_build_id=..., include_inventory=true)` |
| Player's saved loadouts | `loadout_assistant(intent="list"/"get")` |
| Subclass, fragment, or artifact | `subclass_assistant(intent="get"/"options"/"fragments"/"fragment_details"/"artifact")` |
| Activity history or one PGCR | `activity_assistant(intent="history"/"pgcr"/"stats"/"aggregate")` |
| Current vendor or weekly data | `world_assistant(intent="vendor"/"weekly"/"weekly_full")` |
| Community activity/world reference | `activity_assistant(intent="community")` or `world_assistant(intent="community")` |

## Perk search distinction

For “all weapons in my account with Perk X”, use `filter_rolls` and inspect `coverage_complete`, `checked_count`, `unknown_count`, and pagination. A zero result with incomplete coverage is not proof of absence.

For “which weapons in the game can roll Perk X”, use `catalog`. Label the result as Manifest candidates; `owned=false` or `ownership_checked=false` is not an ownership conclusion.

For “what does Perk X do”, use `perk_description`; do not infer an effect from the name.

## Community and account routing

“Community build”, “popular build”, or “Starside build” means `build_assistant(intent="community")`, not `loadout_assistant`. “My saved build/loadout” means `loadout_assistant`. A community result may be matched to the account only after the user asks for that and a concrete `community_build_id` is available.

## Farming-list attachments

Weapon-bearing responses carry a `farming_list` field: an exact-name lookup into the local 刷取清单 (白弹/绿弹/威能紫枪 and 异域武器). Each row gives `tier` (or `scenario_tiers` such as `{"输出": "T0", "高难": "T0.5"}`, or a `role` label), `frame`, `element`, `perks`, `source`, and `note`, plus `source_ref` with the list name and update date.

Use it when the user asks whether a weapon is worth keeping, farming, or using. Quote the tier together with its list name; these are community ratings, not official data. `unmatched` means the local lists have no such name — never report that as "this weapon is not worth farming". The list comes from local files and may be absent; check `available` before relying on it.
