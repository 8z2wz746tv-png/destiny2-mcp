"""赛季神器：列表、按名/按 hash 查询、层级与模组解析。

以 mixin 挂在 ManifestManager 上。列表与按名查询靠 JSON 子串匹配（依赖紧凑
序列化），模组富化依赖 ItemDefinitionMixin 与 PlugCatalogMixin 的方法。
新方法加在这里，不要再往 manifest.py 堆。
"""

from __future__ import annotations

import json


class ArtifactCatalogMixin:
    """神器的只读查询与结构解析。"""

    def get_all_artifacts(self) -> list[dict]:
        """获取所有可用的赛季神器列表。

        Returns:
            [
                {
                    "name": "好奇之器",
                    "hash": -1600062152,
                    "description": "...",
                },
                ...
            ]
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return []

        # 查询 bucket=1506418338 的神器物品
        # 注意：有些物品名称为空，需要过滤掉
        cur = conn.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)

        results = []
        seen_names = set()
        for row in cur:
            data = json.loads(row["json"])
            name = (data.get("displayProperties") or {}).get("name", "")
            desc = (data.get("displayProperties") or {}).get("description", "")

            # 跳过空名称或重复名称
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            results.append({
                "name": name,
                "hash": row["id"],
                "description": desc[:100] if desc else "",
            })

        # 按名称排序
        results.sort(key=lambda x: x["name"])
        return results

    def get_artifact_by_name(self, name: str) -> dict | None:
        """根据名称模糊匹配赛季神器。

        Args:
            name: 神器名称（支持模糊匹配）

        Returns:
            {"name", "hash", "description"} 或 None
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return None

        # 查询 bucket=1506418338 的神器物品
        cur = conn.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)

        # 先尝试精确匹配
        for row in cur:
            data = json.loads(row["json"])
            artifact_name = (data.get("displayProperties") or {}).get("name", "")
            # 跳过空名称
            if not artifact_name:
                continue
            if name == artifact_name:
                desc = (data.get("displayProperties") or {}).get("description", "")
                return {
                    "name": artifact_name,
                    "hash": row["id"],
                    "description": desc,
                }

        # 模糊匹配
        cur.execute("""
            SELECT id, json FROM DestinyInventoryItemDefinition
            WHERE json LIKE '%1506418338%' AND json LIKE '%itemType":28%'
        """)
        for row in cur:
            data = json.loads(row["json"])
            artifact_name = (data.get("displayProperties") or {}).get("name", "")
            # 跳过空名称
            if not artifact_name:
                continue
            if name in artifact_name or artifact_name in name:
                desc = (data.get("displayProperties") or {}).get("description", "")
                return {
                    "name": artifact_name,
                    "hash": row["id"],
                    "description": desc,
                }

        return None

    def get_current_artifact(self) -> dict | None:
        """获取最新的赛季神器（按 hash 排序取最新）。

        Returns:
            {
                "name": "好奇之器",
                "description": "...",
                "hash": -1400744370,
                "tiers": [
                    {
                        "tier_hash": 3144670121,
                        "display_title": "1阶",
                        "min_unlock_points": 0,
                        "mods": [
                            {"name": "xxx", "hash": 123456, "description": "..."},
                            ...
                        ]
                    },
                    ...
                ]
            }
            或 None（如果 manifest 未加载）
        """
        conn = self._zh_conn or self._conn
        if not conn:
            return None

        # 查询所有赛季神器（按 hash 排序取最新）
        cur = conn.execute(
            "SELECT id, json FROM DestinyArtifactDefinition ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None

        data = json.loads(row["json"])
        return self._parse_artifact(row["id"], data)

    def get_artifact_by_hash(self, artifact_hash: int) -> dict | None:
        """根据 hash 获取赛季神器详情。

        Args:
            artifact_hash: 神器的 hash

        Returns:
            同 get_current_artifact 的返回格式，或 None
        """
        from .utils.hash_utils import to_signed

        conn = self._zh_conn or self._conn
        if not conn:
            return None

        signed_hash = to_signed(artifact_hash)
        cur = conn.execute(
            "SELECT id, json FROM DestinyArtifactDefinition WHERE id = ?",
            (signed_hash,)
        )
        row = cur.fetchone()
        if not row:
            return None

        data = json.loads(row["json"])
        return self._parse_artifact(row["id"], data)

    def _parse_artifact(self, artifact_hash: int, data: dict) -> dict:
        """解析赛季神器数据。"""
        from .utils.hash_utils import to_signed

        name = (data.get("displayProperties") or {}).get("name", "")
        desc = (data.get("displayProperties") or {}).get("description", "")

        tiers = []
        for tier in data.get("tiers", []):
            tier_hash = tier.get("tierHash", 0)
            display_title = tier.get("displayTitle", "")
            min_points = tier.get("minimumUnlockPointsUsedRequirement", 0)

            mods = []
            for item in tier.get("items", []):
                item_hash = item.get("itemHash", 0)
                # artifact definition 里的 hash 是 unsigned，需要转换为 signed 查询
                signed_hash = to_signed(item_hash)
                item_def = self.get_item_definition(signed_hash)
                if item_def:
                    mod_name = (item_def.get("displayProperties") or {}).get("name", "")
                    mod_desc = (item_def.get("displayProperties") or {}).get("description", "")

                    # 如果描述为空，尝试从 perks 获取
                    if not mod_desc:
                        perk_descs = []
                        for p in item_def.get("perks", []):
                            perk_hash = p.get("perkHash", 0)
                            if perk_hash:
                                perk_info = self.get_sandbox_perk_description(perk_hash)
                                if perk_info:
                                    perk_desc = perk_info.get("description", "")
                                    if perk_desc:
                                        perk_descs.append(perk_desc)
                        if perk_descs:
                            mod_desc = "; ".join(perk_descs)

                    mods.append({
                        "name": mod_name,
                        "hash": item_hash,  # 返回 unsigned hash 给用户
                        "description": mod_desc,
                    })

            tiers.append({
                "tier_hash": tier_hash,
                "display_title": display_title,
                "min_unlock_points": min_points,
                "mods": mods,
            })

        return {
            "name": name,
            "description": desc,
            "hash": artifact_hash,
            "tiers": tiers,
        }

    def get_artifact_mod_details(self, mod_hash: int) -> dict | None:
        """获取赛季神器模组的详细信息。

        Args:
            mod_hash: 模组的 hash

        Returns:
            {"name", "hash", "description", "perks": [{"name", "description"}]}
            或 None
        """
        item_def = self.get_item_definition(mod_hash)
        if not item_def:
            return None

        name = (item_def.get("displayProperties") or {}).get("name", "")
        desc = (item_def.get("displayProperties") or {}).get("description", "")

        # 获取 perk 效果
        perks = []
        for p in item_def.get("perks", []):
            perk_hash = p.get("perkHash", 0)
            if perk_hash:
                perk_info = self.get_sandbox_perk_description(perk_hash)
                if perk_info:
                    perks.append({
                        "name": perk_info.get("name", ""),
                        "description": perk_info.get("description", ""),
                    })

        # 如果主描述为空，用 perks 描述组合
        if not desc and perks:
            desc = "; ".join(p["description"] for p in perks if p["description"])

        return {
            "name": name,
            "hash": mod_hash,
            "description": desc,
            "perks": perks,
        }
