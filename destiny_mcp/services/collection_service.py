"""Collection service — readable collectible unlock status."""

from __future__ import annotations

from ..bungie_client import BungieClient
from ..exceptions import APIError
from ..logging_config import get_logger
from ..manifest import ManifestManager
from ..player_resolver import PlayerResolver

logger = get_logger(__name__)

_COLLECTIBLE_STATE_FLAGS = {
    1: "未获得",
    2: "已隐藏详情",
    4: "不可见",
    8: "材料不足",
    16: "背包空间不足",
    32: "唯一性冲突",
    64: "无法购买",
}


class CollectionService:
    """Query collectible presentation nodes and format unlock state."""

    def __init__(
        self,
        bungie: BungieClient,
        manifest: ManifestManager,
        resolver: PlayerResolver,
    ) -> None:
        self._bungie = bungie
        self._manifest = manifest
        self._resolver = resolver

    @staticmethod
    def _state_labels(state: int) -> list[str]:
        return [
            label for flag, label in _COLLECTIBLE_STATE_FLAGS.items()
            if state & flag
        ] or ["已获得"]

    def search_collectible_nodes(self, query: str, limit: int = 20) -> dict:
        """Search collectible presentation nodes by localized display text."""
        nodes = self._manifest.search_definitions_by_name(
            "DestinyPresentationNodeDefinition",
            query,
            limit=max(1, min(limit, 100)),
            scan_limit=20000,
        )
        results = []
        for node in nodes:
            display = node.get("displayProperties") or {}
            children = node.get("children") or {}
            results.append({
                "node_hash": int(node.get("hash", 0)),
                "name": display.get("name", ""),
                "description": display.get("description", ""),
                "collectible_count": len(children.get("collectibles", []) or []),
                "presentation_node_count": len(children.get("presentationNodes", []) or []),
                "item_count": len(children.get("records", []) or []),
            })
        return {
            "success": True,
            "query": query,
            "nodes": results,
            "message": f"找到 {len(results)} 个收藏品/展示节点候选。",
        }

    async def get_collectible_item_status(
        self,
        player_name: str,
        item_name: str,
        character: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Search items by name and return collectible unlock states."""
        if not item_name.strip():
            return {"success": False, "message": "必须提供 item_name。"}

        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        if character:
            char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        else:
            profile_for_chars = await self._resolver.get_profile(mid, mtype, [200])
            chars = profile_for_chars.get("characters", {}).get("data", {})
            if not chars:
                raise APIError("读取收藏品", "账号下没有可用角色。")
            char_id = next(iter(chars))

        profile = await self._resolver.get_profile(mid, mtype, [800])
        profile_data = profile.get("profileCollectibles", {}).get("data")
        character_data = profile.get("characterCollectibles", {}).get("data")
        profile_collectibles = (
            profile_data.get("collectibles")
            if isinstance(profile_data, dict)
            else None
        )
        character_collectibles = (
            character_data.get(str(char_id), {}).get("collectibles")
            if isinstance(character_data, dict)
            else None
        )
        if not isinstance(profile_collectibles, dict) and not isinstance(
            character_collectibles,
            dict,
        ):
            raise APIError(
                "读取收藏品",
                "Bungie 未返回收藏状态组件，不能把未知状态解释为未获得。",
            )
        profile_collectibles = profile_collectibles or {}
        character_collectibles = character_collectibles or {}

        candidates = self._manifest.search(item_name, limit=max(1, min(limit, 50)))
        items: list[dict] = []
        for candidate in candidates:
            item_hash = int(candidate.get("itemHash", 0))
            item_def = self._manifest.get_item_definition(item_hash) or {}
            collectible_hash = int(item_def.get("collectibleHash", 0) or 0)
            collectible_def = None
            if collectible_hash:
                collectible_def = self._manifest.get_definition(
                    "DestinyCollectibleDefinition",
                    collectible_hash,
                )
            else:
                collectible_def = self._manifest.find_collectible_by_item_hash(item_hash)
                collectible_hash = int((collectible_def or {}).get("hash", 0) or 0)

            if not collectible_hash:
                items.append({
                    "item_hash": item_hash,
                    "name": candidate.get("name", self._manifest.get_item_name(item_hash)),
                    "collectible_hash": 0,
                    "has_collectible": False,
                    "message": "Manifest 中未找到对应收藏品定义。",
                })
                continue

            state_entry = (
                character_collectibles.get(str(collectible_hash))
                or character_collectibles.get(collectible_hash)
                or profile_collectibles.get(str(collectible_hash))
                or profile_collectibles.get(collectible_hash)
                or {}
            )
            if not isinstance(state_entry.get("state"), int):
                raise APIError(
                    "读取收藏品",
                    f"收藏品 {collectible_hash} 未返回明确状态，不能判断是否已获得。",
                )
            state = state_entry["state"]
            acquired = not bool(state & 1)
            display = (collectible_def or {}).get("displayProperties") or {}
            items.append({
                "item_hash": item_hash,
                "name": candidate.get("name", self._manifest.get_item_name(item_hash)),
                "collectible_hash": collectible_hash,
                "collectible_name": display.get("name", ""),
                "has_collectible": True,
                "acquired": acquired,
                "state": state,
                "state_labels": self._state_labels(state),
            })

        acquired_count = sum(1 for item in items if item.get("acquired"))
        return {
            "success": True,
            "query": item_name,
            "items": items,
            "message": f"找到 {len(items)} 个候选，其中 {acquired_count} 个显示已获得。",
        }

    async def get_collectible_node_status(
        self,
        player_name: str,
        collectible_node_hash: int,
        character: str | None = None,
        include_invisible: bool = False,
        limit: int = 200,
    ) -> dict:
        """Return unlock status for collectibles directly under one presentation node."""
        p = await self._resolver.resolve_player(player_name)
        mid = p["membership_id"]
        mtype = p["membership_type"]

        if character:
            char_id = await self._resolver.resolve_character_id(mid, mtype, character)
        else:
            profile = await self._resolver.get_profile(mid, mtype, [200])
            chars = profile.get("characters", {}).get("data", {})
            if not chars:
                raise APIError("读取收藏品", "账号下没有可用角色。")
            char_id = next(iter(chars))

        result = await self._bungie.get_collectible_node_details(
            mtype,
            mid,
            char_id,
            collectible_node_hash,
            components=[800],
        )
        if not isinstance(result, dict):
            raise APIError("读取收藏品", "响应格式异常。")

        error_code = result.get("ErrorCode")
        if error_code is not None and error_code != 1:
            raise APIError("读取收藏品", result.get("Message", ""))

        response = result.get("Response", result)
        collectible_data = response.get("collectibles", {}).get("data")
        collectibles = (
            collectible_data.get("collectibles")
            if isinstance(collectible_data, dict)
            else None
        )
        if not isinstance(collectibles, dict):
            raise APIError(
                "读取收藏品节点",
                "Bungie 未返回节点收藏状态组件，不能将结果视为空节点。",
            )

        items: list[dict] = []
        counts = {
            "total": 0,
            "acquired": 0,
            "missing": 0,
            "invisible": 0,
        }
        for collectible_hash, component in collectibles.items():
            if not isinstance(component, dict) or not isinstance(component.get("state"), int):
                raise APIError("读取收藏品节点", "部分收藏品状态缺失，无法给出完整结论。")
            state = component["state"]
            invisible = bool(state & 4)
            counts["total"] += 1
            if invisible:
                counts["invisible"] += 1
                if not include_invisible:
                    continue

            acquired = not bool(state & 1)
            counts["acquired" if acquired else "missing"] += 1
            definition = self._manifest.get_definition(
                "DestinyCollectibleDefinition",
                int(collectible_hash),
            ) or {}
            item_hash = definition.get("itemHash", 0)
            item_def = self._manifest.get_item_definition(item_hash) if item_hash else None
            display = definition.get("displayProperties", {}) or {}
            item_display = (item_def or {}).get("displayProperties", {}) if item_def else {}
            name = (
                item_display.get("name")
                or display.get("name")
                or self._manifest.get_item_name(item_hash)
                if item_hash
                else f"Collectible({collectible_hash})"
            )
            items.append({
                "collectible_hash": int(collectible_hash),
                "item_hash": item_hash,
                "name": name,
                "acquired": acquired,
                "state": state,
                "state_labels": self._state_labels(state),
            })

        items.sort(key=lambda item: (item["acquired"], item["name"]))
        limited_items = items[: max(1, min(limit, 500))]
        node_def = self._manifest.get_definition(
            "DestinyPresentationNodeDefinition",
            collectible_node_hash,
        ) or {}
        node_name = (node_def.get("displayProperties") or {}).get(
            "name",
            f"PresentationNode({collectible_node_hash})",
        )

        return {
            "success": True,
            "node_hash": collectible_node_hash,
            "node_name": node_name,
            "counts": counts,
            "items": limited_items,
            "message": (
                f"{node_name}: 共 {counts['total']} 项，"
                f"已获得 {counts['acquired']}，未获得 {counts['missing']}。"
            ),
        }
