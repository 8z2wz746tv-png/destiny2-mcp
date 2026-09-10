# Evidence and completeness

Every answer must preserve the scope of the returned data.

## Check responses first

Inspect `ok`, `data`, `error`, `warnings`, and any `coverage` or `pagination` fields before interpreting content. On `ok=false`, report the returned error and stop that line of analysis. An empty field is not permission to use remembered game knowledge.

## Keep sources separate

- Current instance data proves what was read on that instance.
- A Perk pool proves possible candidates, not the current roll.
- A Manifest/catalog result proves game definitions, not ownership.
- Starside proves what the bundled author document or optional local snapshot records, not official endorsement, current live values, or popularity unless a real metric is returned.
- Bungie profile data proves the account state at the time of the request.

## Pagination and coverage

Follow `next_offset` exactly and preserve the original filters. Do not call the same page repeatedly or treat one page as a full-account result. Keep `has_more`, `coverage_complete`, `scan_complete`, and unknown counts in the final explanation when they affect the conclusion.

## Unknown is not missing

Use separate labels for confirmed present; confirmed absent within a complete checked scope; not found in an incomplete scope; unresolved name or Manifest match; not read or unavailable; and not verified by the tool.

Never turn `null` selection rate into 0%, an unresolved name into “missing”, or a current Perk mismatch into proof that an item cannot roll that Perk.

## Output discipline

Put warnings next to the affected conclusion. Preserve source URL or local document path, page update time, snapshot metadata, PvE/PvP/enhanced/uncertain markers, and external-link-only status when returned. An absent source URL must not be fabricated. A document without `updated_at` cannot establish a current live rotation. Do not expose API keys, OAuth secrets, access tokens, refresh tokens, or authorization codes.
