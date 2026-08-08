"""Build Scorer — multi-objective ranking of BuildCandidates.

Scores builds on:
  1. Stat attainment rate — fraction of targets met
  2. Total stats — sum of all 6 stats (higher is better)
  3. Waste penalty — stats above the target are penalized (diminishing returns)
     EXCEPT for the priority (dump) stat — excess there is desired.

The composite score is normalized so that meeting targets dominates
total stats, which dominates waste avoidance.
"""

from __future__ import annotations

from ..logging_config import get_logger
from .constants import STAT_NAMES
from .models import BuildCandidate, BuildConstraints

logger = get_logger(__name__)


def score(candidate: BuildCandidate, constraints: BuildConstraints) -> float:
    """Score a build candidate against the constraints.

    Scoring weights (chosen so attainment dominates everything else):
    - Attainment: 1000 points per target met
    - Total bonus: 0.1 points per total stat point
    - Waste penalty: -0.5 points per point of excess (above target)
      EXCEPT for the priority stat — excess there is desired, not waste.

    Args:
        candidate: A valid build candidate.
        constraints: The target constraints.

    Returns:
        Composite score (higher = better). Can be negative if waste is high.
    """
    num_targets = 0
    num_met = 0
    total_waste = 0
    total_stats = 0
    priority_idx = constraints.priority_stat_index

    for i, stat_name in enumerate(STAT_NAMES):
        actual = candidate.stat(stat_name)
        target = getattr(constraints, f"{stat_name}_min")
        total_stats += actual
        if target > 0:
            num_targets += 1
            if actual >= target:
                num_met += 1
                # Only count waste for non-priority stats
                if i != priority_idx:
                    total_waste += actual - target

    # Attainment: fraction of targets met
    att_rate = num_met / max(1, num_targets)
    attainment_score = att_rate * 1000.0

    # Total stats: small bonus per point
    total_bonus = total_stats * 0.1

    # Waste: penalty for excess (skip priority stat — excess is desired)
    waste_penalty = total_waste * 0.5

    result = attainment_score + total_bonus - waste_penalty

    logger.debug(
        "Score: att=%.2f met=%d/%d waste=%d total=%d → %.1f",
        att_rate, num_met, num_targets, total_waste, total_stats, result,
    )
    return result
