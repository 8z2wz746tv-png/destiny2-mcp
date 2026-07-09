"""WishList service — DIM wish list data for god roll annotations.

Loads curated god roll recommendations from DIM community wish lists
and provides scoring/lookup for weapon perk comparison.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class RollScore:
    """Score for a weapon instance's perks against god roll recommendations."""

    pve_hits: int = 0
    pve_total: int = 0
    pvp_hits: int = 0
    pvp_total: int = 0

    @property
    def best_tag(self) -> str:
        """Which tag has the best hit rate."""
        pve_pct = self.pve_hits / self.pve_total if self.pve_total else 0
        pvp_pct = self.pvp_hits / self.pvp_total if self.pvp_total else 0
        if pve_pct >= pvp_pct:
            return "pve"
        return "pvp"

    @property
    def best_pct(self) -> float:
        """Best hit percentage."""
        pve_pct = self.pve_hits / self.pve_total if self.pve_total else 0
        pvp_pct = self.pvp_hits / self.pvp_total if self.pvp_total else 0
        return max(pve_pct, pvp_pct)

    def __str__(self) -> str:
        parts = []
        if self.pve_total:
            parts.append(f"PvE {self.pve_hits}/{self.pve_total}")
        if self.pvp_total:
            parts.append(f"PvP {self.pvp_hits}/{self.pvp_total}")
        return " | ".join(parts) if parts else "无数据"


@dataclass
class GodRollPerks:
    """God roll perk sets for a weapon, split by tag."""

    item_hash: int
    pve_perks: set[int] = field(default_factory=set)
    pvp_perks: set[int] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)


class WishListService:
    """Loads and queries DIM wish list data."""

    def __init__(self, data_path: Path | str | None = None):
        self._weapons: dict[int, GodRollPerks] = {}
        if data_path:
            self._load(Path(data_path))

    def _load(self, path: Path) -> None:
        """Load wish list JSON file."""
        if not path.exists():
            logger.warning("Wish list file not found: %s", path)
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load wish list: %s", e)
            return

        rolls_data = data.get("rolls", {})
        for item_hash_str, rolls in rolls_data.items():
            item_hash = int(item_hash_str)
            grp = GodRollPerks(item_hash=item_hash)

            for roll in rolls:
                # Parse compact format: {"p": "hash1,hash2,...", "t": ["pve"], "s": "..."}
                perks_str = roll.get("p", "")
                perk_hashes = {int(h) for h in perks_str.split(",") if h.strip()}
                tags = roll.get("t", [])
                source = roll.get("s", "")

                for tag in tags:
                    tag_lower = tag.lower().strip()
                    if tag_lower == "pve":
                        grp.pve_perks.update(perk_hashes)
                    elif tag_lower == "pvp":
                        grp.pvp_perks.update(perk_hashes)

                if source and source not in grp.sources:
                    grp.sources.append(source)

            self._weapons[item_hash] = grp

        logger.info(
            "Loaded wish list: %d weapons, %d PvE perks, %d PvP perks",
            len(self._weapons),
            sum(len(g.pve_perks) for g in self._weapons.values()),
            sum(len(g.pvp_perks) for g in self._weapons.values()),
        )

    def has_data(self, item_hash: int) -> bool:
        """Check if we have wish list data for this weapon."""
        return item_hash in self._weapons

    def is_god_roll_perk(self, item_hash: int, perk_hash: int) -> dict[str, bool]:
        """Check if a perk is in the god roll recommendations.

        Returns {"pve": bool, "pvp": bool}.
        """
        grp = self._weapons.get(item_hash)
        if not grp:
            return {"pve": False, "pvp": False}
        return {
            "pve": perk_hash in grp.pve_perks,
            "pvp": perk_hash in grp.pvp_perks,
        }

    def get_god_roll_perks(self, item_hash: int) -> GodRollPerks | None:
        """Get all god roll perks for a weapon."""
        return self._weapons.get(item_hash)

    def score_roll(self, item_hash: int, perk_hashes: list[int]) -> RollScore:
        """Score a set of perks against god roll recommendations."""
        grp = self._weapons.get(item_hash)
        if not grp:
            return RollScore()

        perk_set = set(perk_hashes)
        return RollScore(
            pve_hits=len(perk_set & grp.pve_perks),
            pve_total=len(grp.pve_perks),
            pvp_hits=len(perk_set & grp.pvp_perks),
            pvp_total=len(grp.pvp_perks),
        )
