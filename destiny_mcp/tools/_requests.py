"""Per-intent validation behind the stable aggregate tool signatures."""

from functools import wraps
from inspect import signature
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

from ._responses import error_response

PlayerIntent = Literal["profile", "get_profile", "角色", "档案", "search", "search_player", "find", "find_players", "fuzzy"]
InventoryIntent = Literal[
    "summary", "summarize", "概况", "duplicates", "duplicate_weapons", "find_duplicates", "重复武器",
    "get", "inventory", "list", "search", "find_item", "type", "search_type", "move", "transfer",
    "equip", "equip_many", "equip_items", "pull_postmaster", "lock", "track_quest", "quest_tracking",
]
WeaponIntent = Literal[
    "analyze", "catalog", "search_catalog", "all_weapons", "global", "search_all", "filter_rolls",
    "compare", "compare_duplicates", "perk_pool", "perks", "god_roll", "popularity", "selection_rates",
    "perk_selection", "selection", "usage_rates", "type", "info", "stats", "perk_description", "catalyst",
]
BuildIntent = Literal["recommend", "find", "analyze", "farm_target", "equip_build", "armor_mods", "exotic_armor", "set_bonus"]
LoadoutIntent = Literal["list", "get", "save", "delete", "equip_loadout", "search_identifiers", "snapshot_official", "update_official_identifiers", "clear_official"]
SubclassIntent = Literal["get", "subclass", "modify", "options", "fragments", "fragment_details", "artifact", "artifact_mod", "equip_artifact_mod"]
ActivityIntent = Literal["history", "pgcr", "stats", "career", "historical_stats", "weapon_history", "weapons", "weapon_usage", "weapon_leaderboard", "aggregate", "activity_aggregate", "activity_stats", "leaderboards", "leaderboard", "clan_leaderboards"]
WorldIntent = Literal["weekly", "weekly_full", "vendor", "search_collectible_nodes", "collectible_node", "collectible_item"]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class IntentRequest(BaseModel):
    intent: str

    def require(self, *fields: str) -> None:
        for field in fields:
            value = getattr(self, field)
            if not value or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"intent={self.intent} requires {field}.")


class InventoryRequest(IntentRequest):
    item_name: str = ""
    item_instance_id: str = ""
    item_instance_ids: list[NonEmpty] | None = None
    destination: str = ""
    to_character: str = ""
    character: str = ""
    equip: bool = False

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.intent == "move":
            self.require("item_name", "destination")
            if self.equip and self.destination.strip().lower() in {"vault", "仓库"}:
                raise ValueError("Cannot equip an item in the vault.")
        elif self.intent == "transfer":
            self.require("item_instance_id", "to_character")
        elif self.intent == "equip":
            self.require("item_instance_id", "character")
        elif self.intent in {"equip_many", "equip_items"}:
            self.require("item_instance_ids", "character")
            instance_ids = self.item_instance_ids or []
            if len(set(instance_ids)) != len(instance_ids):
                raise ValueError("item_instance_ids must be unique.")
        elif self.intent in {"pull_postmaster", "lock", "track_quest", "quest_tracking"}:
            self.require("item_instance_id")
        return self


class LoadoutRequest(IntentRequest):
    character: str = ""
    name: str = ""
    loadout_id: str = ""
    slot_number: int = Field(default=1, ge=1, le=10)
    name_hash: int | None = None
    icon_hash: int | None = None
    color_hash: int | None = None

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.intent == "save":
            self.require("name", "character")
        elif self.intent in {"delete", "equip_loadout"}:
            self.require("loadout_id")
        elif self.intent in {"snapshot_official", "update_official_identifiers", "clear_official"}:
            self.require("character")
        if self.intent == "update_official_identifiers" and all(
            value is None for value in (self.name_hash, self.icon_hash, self.color_hash)
        ):
            raise ValueError("Provide at least one name_hash, icon_hash or color_hash.")
        return self


class SubclassRequest(IntentRequest):
    character: str = ""
    changes: dict[NonEmpty, NonEmpty] | None = None
    artifact_mod_hash: int = 0

    @model_validator(mode="after")
    def check_action(self) -> Self:
        if self.intent == "modify":
            self.require("character", "changes")
        elif self.intent == "equip_artifact_mod":
            self.require("character")
            if self.artifact_mod_hash <= 0:
                raise ValueError("artifact_mod_hash must be positive.")
        return self


def validate_request(model: type[IntentRequest]):
    def decorator(function):
        parameters = signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            arguments = parameters.bind(*args, **kwargs)
            arguments.apply_defaults()
            values = dict(arguments.arguments)
            values["intent"] = (values.get("intent") or parameters.parameters["intent"].default).strip().lower()
            try:
                model.model_validate(values)
            except ValidationError as exc:
                messages = "; ".join(error["msg"] for error in exc.errors(include_input=False))
                return error_response("invalid_arguments", messages)
            return await function(*args, **kwargs)

        return wrapped

    return decorator
