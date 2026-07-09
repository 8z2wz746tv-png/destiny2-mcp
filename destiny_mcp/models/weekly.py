"""Weekly reset models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WeeklyActivity(BaseModel):
    """An activity within a milestone."""

    name: str = Field(default="", description="Activity name")
    activity_type: str = Field(default="", description="Activity type (Raid, Strike, etc.)")


class WeeklyMilestone(BaseModel):
    """A single weekly milestone."""

    milestone_hash: int = Field(description="Milestone definition hash")
    name: str = Field(default="", description="Milestone display name")
    description: str = Field(default="", description="Milestone description")
    category: str = Field(default="", description="nightfall / trials / raid / crucible / other")
    activities: list[WeeklyActivity] = Field(default_factory=list)
    end_date: str = Field(default="", description="End time (ISO)")


class WeeklyResetResponse(BaseModel):
    """get_weekly_reset tool response."""

    milestones: list[WeeklyMilestone] = Field(default_factory=list)
    reset_time: str = Field(default="", description="Next weekly reset time (ISO)")
