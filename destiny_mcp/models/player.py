"""Player and profile models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterInfo(BaseModel):
    """A Destiny 2 character summary."""

    id: str = Field(description="Character ID")
    class_type: int = Field(description="classType: 0=Titan, 1=Hunter, 2=Warlock")
    class_name: str = Field(description="Human-readable class name")
    light: int = Field(description="Current power/light level")
    emblem_path: str | None = Field(default=None, description="Emblem icon URL")
    last_played: str | None = Field(default=None, description="Date last played")


class PlayerInfo(BaseModel):
    """Resolved player information."""

    display_name: str = Field(description="Bungie display name with code")
    membership_id: str = Field(description="Destiny membership ID")
    membership_type: int = Field(description="Platform membership type")


class ProfileResponse(BaseModel):
    """get_profile tool response."""

    display_name: str = Field(default="", description="Bungie display name with code")
    membership_id: str = Field(description="Destiny membership ID")
    membership_type: int = Field(default=0, description="Platform membership type")
    characters: list[CharacterInfo] = Field(default_factory=list)
