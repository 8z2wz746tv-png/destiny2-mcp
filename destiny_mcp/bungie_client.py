"""Bungie API client wrapper — token lifecycle, core API calls."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path

import httpx

import aiobungie

from . import config
from .exceptions import APIError, AuthenticationError, BungieServiceUnavailableError, ManifestError
from .logging_config import get_logger

logger = get_logger(__name__)

_VENDOR_COMPONENTS_FULL = [400, 401, 402, 300, 301, 302, 304, 305, 306, 307, 308, 309, 310]
_VENDOR_COMPONENTS_BASIC = [400, 402]
_VENDOR_ITEM_COMPONENTS = [305, 310]


def _is_insufficient_privileges(exc: aiobungie.HTTPError) -> bool:
    """Return whether Bungie rejected a request due to missing OAuth scope."""
    error_status = str(getattr(exc, "error_status", "") or "")
    if error_status == "InsufficientPrivileges":
        return True
    return "InsufficientPrivileges" in str(exc)


def _is_bungie_service_unavailable(exc: aiobungie.HTTPError) -> bool:
    """Return whether Bungie is temporarily unavailable or under maintenance."""
    http_status = getattr(exc, "http_status", None)
    try:
        if int(http_status) == 503:
            return True
    except (TypeError, ValueError):
        pass

    status_parts = [
        str(getattr(exc, "error_status", "") or ""),
        str(getattr(exc, "message", "") or ""),
        str(exc),
    ]
    return any(
        marker in part
        for part in status_parts
        for marker in ("SystemDisabled", "ServiceUnavailable", "Serviceunavailable")
    )


def _raise_bungie_unavailable(exc: aiobungie.HTTPError, operation: str) -> None:
    if _is_bungie_service_unavailable(exc):
        logger.warning("Bungie unavailable during %s: %s", operation, exc)
        raise BungieServiceUnavailableError(operation) from exc


def _http_error_code(exc: aiobungie.HTTPError) -> int:
    code = getattr(exc, "error_code", None)
    if code:
        return int(code)
    status = getattr(exc, "http_status", 0)
    return int(getattr(status, "value", status) or 0)


def _bungie_unavailable_result(exc: aiobungie.HTTPError, operation: str) -> dict | None:
    if not _is_bungie_service_unavailable(exc):
        return None
    logger.warning("Bungie unavailable during %s: %s", operation, exc)
    return {
        "ErrorCode": 503,
        "Message": "Bungie 官方接口暂时不可用，可能正在维护或限流。请稍后重试。",
    }


class BungieClient:
    """Encapsulates aiobungie RESTClient with automatic token management."""

    def __init__(self, token_dir: Path | None = None) -> None:
        self._rest: aiobungie.RESTClient | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_dir = token_dir  # Per-user token directory override
        self._token_lock = asyncio.Lock()

    # ── Token persistence ──────────────────────────────────────────

    def _token_path(self) -> Path:
        base = self._token_dir if self._token_dir else config.DESTINY_TOKEN_PATH
        return base / "tokens.json"

    async def _load_tokens(self) -> bool:
        """Load tokens from disk. Returns True if valid tokens loaded."""
        path = self._token_path()
        if not path.exists():
            logger.info("No token file at %s — OAuth setup required", path)
            return False
        try:
            os.chmod(path, 0o600)
            data = json.loads(path.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._token_expires_at = data.get("expires_at", 0)
            if self._access_token and time.time() < self._token_expires_at - 300:
                logger.info("Token loaded from disk, valid until %s",
                           time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self._token_expires_at)))
                return True
            if self._refresh_token:
                logger.info("Access token expired, attempting refresh...")
                ok = await self._refresh_access_token()
                if ok:
                    logger.info("Token refreshed successfully")
                return ok
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse token file: %s", e)
        return False

    def _save_tokens(self) -> None:
        """Persist tokens to disk, preserving membership_id if present on disk."""
        path = self._token_path()
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (json.JSONDecodeError, KeyError):
                logger.warning("Could not parse existing token file, overwriting: %s", path)
        data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._token_expires_at,
            "membership_id": existing.get("membership_id", ""),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        logger.debug("Tokens saved to %s", path)

    async def _refresh_access_token(self) -> bool:
        """Use refresh_token to get a new access_token. Returns True on success."""
        if not self._refresh_token or not self._rest:
            return False
        try:
            resp = await self._rest.refresh_access_token(self._refresh_token)
            self._access_token = resp.access_token
            self._token_expires_at = time.time() + resp.expires_in
            if resp.refresh_token:
                self._refresh_token = resp.refresh_token
            self._save_tokens()
            logger.info("Access token refreshed successfully")
            return True
        except aiobungie.HTTPError as e:
            logger.error("Token refresh failed: %s", e)
            return False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize the REST client and authenticate."""
        config.validate_credentials()
        logger.info("Starting BungieClient...")
        self._rest = aiobungie.RESTClient(
            config.BUNGIE_API_KEY,
            client_secret=config.BUNGIE_CLIENT_SECRET,
            client_id=int(config.BUNGIE_CLIENT_ID),
        )
        self._rest.open()
        try:
            if not await self._load_tokens():
                raise AuthenticationError(
                    "No valid Destiny OAuth tokens found. "
                    "Run the OAuth setup script first:\n"
                    "  python scripts/oauth_setup.py\n"
                    "or after installation:\n"
                    "  destiny-mcp-oauth"
                )
        except BaseException:
            await self.close()
            raise
        logger.info("BungieClient authenticated")

    async def close(self) -> None:
        rest = self._rest
        self._rest = None
        if rest:
            await rest.close()
            logger.info("BungieClient closed")

    async def get_access_token(self) -> str:
        """Current valid access token (async — may refresh if expired)."""
        if not self._access_token:
            raise AuthenticationError("Not authenticated. Call start() first.")
        # Refresh if expired
        if time.time() >= self._token_expires_at - 300:
            async with self._token_lock:
                if time.time() >= self._token_expires_at - 300:
                    if not await self._refresh_access_token():
                        raise AuthenticationError("Token expired and refresh failed.")
        return self._access_token  # type: ignore[return-value]

    @property
    def rest(self) -> aiobungie.RESTClient:
        if not self._rest:
            raise AuthenticationError("Client not started. Call start() first.")
        return self._rest

    # ── Public API wrappers ────────────────────────────────────────

    async def search_player(
        self, display_name: str, display_name_code: int | None = None
    ) -> list[dict]:
        """Resolve a Bungie name to membership info.

        If name includes '#', splits on the last '#' automatically.
        """
        if "#" in display_name:
            parts = display_name.rsplit("#", 1)
            display_name = parts[0]
            try:
                display_name_code = int(parts[1])
            except (ValueError, TypeError):
                logger.warning("Non-numeric Bungie name code '%s', defaulting to 0", parts[1])
                display_name_code = 0

        logger.debug("API call: SearchDestinyPlayerByBungieName(%s#%s)",
                     display_name, display_name_code or 0)
        try:
            result = await self.rest.static_request(
                "POST",
                "Destiny2/SearchDestinyPlayerByBungieName/-1/",
                auth=await self.get_access_token(),
                json={
                    "displayName": display_name,
                    "displayNameCode": display_name_code or 0,
                },
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "搜索 Bungie 玩家")
            raise
        # static_request unwraps the Response envelope, result is already a list
        players = result if isinstance(result, list) else result.get("Response", [])
        logger.debug("SearchDestinyPlayer returned %d result(s)", len(players))
        return [
            {
                "display_name": f"{p['displayName']}#{p.get('displayNameCode', '')}",
                "membership_id": p["membershipId"],
                "membership_type": p["membershipType"],
                "cross_save_platform": p.get("crossSaveOverride", 0),
            }
            for p in players
        ]

    async def get_current_destiny_membership(self) -> dict:
        """Return the active Destiny membership for the current OAuth token."""
        logger.debug("API call: GetMembershipsForCurrentUser()")
        try:
            result = await self.rest.static_request(
                "GET",
                "User/GetMembershipsForCurrentUser/",
                auth=await self.get_access_token(),
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取当前 Bungie 账号")
            raise
        response = result.get("Response", result) if isinstance(result, dict) else {}
        memberships = response.get("destinyMemberships", [])
        if not memberships:
            raise AuthenticationError("当前 OAuth token 没有关联 Destiny 账号，请重新登录 Bungie。")

        active = next(
            (
                m for m in memberships
                if m.get("crossSaveOverride")
                and int(m.get("membershipType", 0)) == int(m.get("crossSaveOverride", 0))
            ),
            memberships[0],
        )
        name = active.get("displayName") or active.get("bungieGlobalDisplayName") or ""
        code = active.get("displayNameCode") or active.get("bungieGlobalDisplayNameCode") or ""
        display_name = f"{name}#{code}" if name and code else name
        return {
            "display_name": display_name,
            "membership_id": active["membershipId"],
            "membership_type": active["membershipType"],
            "cross_save_platform": active.get("crossSaveOverride", 0),
        }

    async def get_profile(
        self, membership_id: str, membership_type: int, components: list[int]
    ) -> dict:
        """Fetch Destiny profile data with specified components."""
        comps = [aiobungie.ComponentType(c) for c in components]
        comp_names = [c.name for c in comps]
        logger.debug("API call: GetProfile(mid=%s, type=%s, comps=%s)",
                     membership_id, membership_type, comp_names)
        token = await self.get_access_token()
        try:
            result = await self.rest.fetch_profile(
                int(membership_id),
                aiobungie.MembershipType(membership_type),
                comps,
                auth=token,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取 Destiny 档案")
            raise
        logger.debug("GetProfile returned (chars=%s)",
                     result.get("characters", {}).get("data", {}).keys())
        return result

    async def transfer_item(
        self,
        item_instance_id: str,
        item_hash: int,
        character_id: str,
        membership_type: int,
        *,
        to_vault: bool,
        stack_size: int = 1,
    ) -> dict:
        """Transfer an item between a character and the vault.

        Uses the aiobungie RESTClient.transfer_item which calls Bungie's
        TransferItem endpoint. Returns the API response dict.

        Args:
            to_vault: True = character → vault, False = vault → character
        """
        try:
            token = await self.get_access_token()
            await self.rest.transfer_item(
                token,
                int(item_instance_id),
                item_hash,
                int(character_id),
                aiobungie.MembershipType(membership_type),
                stack_size=stack_size,
                vault=to_vault,
            )
            logger.debug("TransferItem OK: item=%s to_vault=%s", item_instance_id, to_vault)
            return {"ErrorCode": 1, "Message": "Ok"}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, "转移物品")
            if unavailable:
                return unavailable
            code = _http_error_code(exc)
            logger.error("TransferItem failed: item=%s error=%s msg=%s", item_instance_id, code, exc)
            return {"ErrorCode": code, "Message": str(exc)}

    async def _post_action(
        self,
        path: str,
        payload: dict,
        operation: str,
    ) -> dict:
        """POST a Bungie action endpoint and normalize the action result."""
        try:
            result = await self.rest.static_request(
                "POST",
                path,
                auth=await self.get_access_token(),
                json=payload,
            )
            # aiobungie already unwraps the Bungie Response envelope.
            return {"ErrorCode": 1, "Message": "Ok", "Response": result}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, operation)
            if unavailable:
                return unavailable
            code = _http_error_code(exc)
            logger.error("%s failed: error=%s msg=%s", operation, code, exc)
            return {"ErrorCode": code, "Message": str(exc)}

    async def pull_from_postmaster(
        self,
        item_instance_id: str,
        item_hash: int,
        character_id: str,
        membership_type: int,
        *,
        stack_size: int = 1,
    ) -> dict:
        """Pull an item from a character's postmaster into inventory."""
        return await self._post_action(
            "Destiny2/Actions/Items/PullFromPostmaster/",
            {
                "itemReferenceHash": item_hash,
                "stackSize": stack_size,
                "itemId": int(item_instance_id),
                "characterId": int(character_id),
                "membershipType": membership_type,
            },
            "从邮政官取回物品",
        )

    async def set_item_lock_state(
        self,
        item_instance_id: str,
        character_id: str,
        membership_type: int,
        *,
        state: bool,
    ) -> dict:
        """Lock or unlock an item instance."""
        return await self._post_action(
            "Destiny2/Actions/Items/SetLockState/",
            {
                "state": state,
                "itemId": int(item_instance_id),
                "characterId": int(character_id),
                "membershipType": membership_type,
            },
            "设置物品锁定状态",
        )

    async def set_quest_tracked_state(
        self,
        item_instance_id: str,
        character_id: str,
        membership_type: int,
        *,
        state: bool,
    ) -> dict:
        """Track or untrack a quest/bounty item instance."""
        return await self._post_action(
            "Destiny2/Actions/Items/SetTrackedState/",
            {
                "state": state,
                "itemId": int(item_instance_id),
                "characterId": int(character_id),
                "membershipType": membership_type,
            },
            "设置任务追踪状态",
        )

    async def equip_item(
        self,
        item_instance_id: str,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Equip an item on a character."""
        try:
            token = await self.get_access_token()
            await self.rest.equip_item(
                token,
                int(item_instance_id),
                int(character_id),
                aiobungie.MembershipType(membership_type),
            )
            logger.debug("EquipItem OK: item=%s char=%s", item_instance_id, character_id)
            return {"ErrorCode": 1, "Message": "Ok"}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, "装备物品")
            if unavailable:
                return unavailable
            code = _http_error_code(exc)
            logger.error("EquipItem failed: item=%s char=%s error=%s msg=%s",
                        item_instance_id, character_id, code, exc)
            return {"ErrorCode": code, "Message": str(exc)}

    async def equip_items(
        self,
        item_instance_ids: list[str],
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Equip multiple items on a character in one Bungie action."""
        return await self._post_action(
            "Destiny2/Actions/Items/EquipItems/",
            {
                "itemIds": [int(item_id) for item_id in item_instance_ids],
                "characterId": int(character_id),
                "membershipType": membership_type,
            },
            "批量装备物品",
        )

    async def insert_socket_plug_free(
        self,
        item_instance_id: str,
        plug_item_hash: int,
        socket_index: int,
        socket_array_type: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Insert a free socket plug (subclass abilities, perk switching, etc.).

        Uses Bungie's InsertSocketPlugFree endpoint. Free plugs include
        subclass abilities (super, melee, grenade, aspects, fragments)
        and weapon perk switching.

        Args:
            item_instance_id: The item instance ID (e.g. subclass instance).
            plug_item_hash: The plug item hash to insert.
            socket_index: The socket index on the item.
            socket_array_type: 0 = default, 1 = reusable.
            character_id: The character ID.
            membership_type: Platform membership type.
        """
        try:
            plug = aiobungie.builders.PlugSocketBuilder()
            plug.set_plug_item(plug_item_hash)
            plug.set_socket_index(socket_index)
            plug.set_socket_array(socket_array_type)
            token = await self.get_access_token()
            await self.rest.insert_socket_plug_free(
                token,
                instance_id=int(item_instance_id),
                plug=plug,
                character_id=int(character_id),
                membership_type=aiobungie.MembershipType(membership_type),
            )
            logger.debug(
                "InsertSocketPlugFree OK: item=%s plug=%s socket=%s",
                item_instance_id, plug_item_hash, socket_index,
            )
            return {"ErrorCode": 1, "Message": "Ok"}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, "插入免费插槽插件")
            if unavailable:
                return unavailable
            code = _http_error_code(exc)
            logger.error(
                "InsertSocketPlugFree failed: item=%s plug=%s error=%s msg=%s",
                item_instance_id, plug_item_hash, code, exc,
            )
            return {"ErrorCode": code, "Message": str(exc)}

    async def insert_socket_plug(
        self,
        item_instance_id: str,
        plug_item_hash: int,
        socket_index: int,
        socket_array_type: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Insert a plug that costs energy (armor mods).

        Uses Bungie's InsertSocketPlug endpoint. This is for plugs that
        cost energy to equip, like armor stat mods.

        Args:
            item_instance_id: The item instance ID (e.g. armor instance).
            plug_item_hash: The plug item hash to insert.
            socket_index: The socket index on the item.
            socket_array_type: 0 = default, 1 = reusable.
            character_id: The character ID.
            membership_type: Platform membership type.
        """
        try:
            token = await self.get_access_token()
            # Use static_request — membershipType goes in the body, not URL
            await self.rest.static_request(
                "POST",
                "Destiny2/Actions/Items/InsertSocketPlug/",
                auth=token,
                json={
                    "plug": {
                        "plugItemHash": plug_item_hash,
                        "socketIndex": socket_index,
                        "socketArrayType": socket_array_type,
                    },
                    "itemInstanceId": int(item_instance_id),
                    "characterId": int(character_id),
                    "membershipType": membership_type,
                },
            )
            logger.debug(
                "InsertSocketPlug OK: item=%s plug=%s socket=%s",
                item_instance_id, plug_item_hash, socket_index,
            )
            return {"ErrorCode": 1, "Message": "Ok"}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, "插入插槽插件")
            if unavailable:
                return unavailable
            logger.error(
                "InsertSocketPlug failed: item=%s plug=%s error=%s",
                item_instance_id, plug_item_hash, exc,
            )
            return {"ErrorCode": _http_error_code(exc), "Message": str(exc)}

    # ── Loadouts ────────────────────────────────────────────────────────

    async def fetch_loadouts(
        self,
        membership_id: str,
        membership_type: int,
    ) -> dict:
        """Fetch native loadouts for all characters.

        Bungie API: GetProfile with component 206 (CHARACTER_LOADOUTS).

        Returns:
            Raw profile response with characterLoadouts.data populated.
        """
        return await self.get_profile(
            membership_id, membership_type, components=[200, 206]
        )

    async def equip_loadout(
        self,
        loadout_index: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Equip a native Bungie loadout.

        Uses static_request to avoid aiobungie bug (membership_type vs membershipType).

        Returns:
            API response dict with ErrorCode.
        """
        try:
            result = await self.rest.static_request(
                "POST",
                f"Destiny2/Actions/Loadouts/EquipLoadout/{membership_type}/",
                auth=await self.get_access_token(),
                json={
                    "loadoutIndex": loadout_index,
                    "characterId": int(character_id),
                    "membershipType": membership_type,
                },
            )
            code = result.get("ErrorCode", 0)
            logger.debug("EquipLoadout OK: index=%s char=%s", loadout_index, character_id)
            return {"ErrorCode": code, "Message": result.get("Message", "Ok")}
        except aiobungie.HTTPError as exc:
            unavailable = _bungie_unavailable_result(exc, "装备 Bungie 配装")
            if unavailable:
                return unavailable
            code = _http_error_code(exc)
            logger.error("EquipLoadout failed: index=%s error=%s", loadout_index, exc)
            return {"ErrorCode": code, "Message": str(exc)}

    async def snapshot_loadout(
        self,
        loadout_index: int,
        character_id: str,
        membership_type: int,
        *,
        name_hash: int | None = None,
        icon_hash: int | None = None,
        color_hash: int | None = None,
    ) -> dict:
        """Save the character's current equipment into an official Bungie loadout slot."""
        payload = {
            "loadoutIndex": loadout_index,
            "characterId": int(character_id),
            "membershipType": membership_type,
        }
        if name_hash is not None:
            payload["nameHash"] = name_hash
        if icon_hash is not None:
            payload["iconHash"] = icon_hash
        if color_hash is not None:
            payload["colorHash"] = color_hash
        return await self._post_action(
            "Destiny2/Actions/Loadouts/SnapshotLoadout/",
            payload,
            "保存官方配装槽",
        )

    async def update_loadout_identifiers(
        self,
        loadout_index: int,
        character_id: str,
        membership_type: int,
        *,
        name_hash: int | None = None,
        icon_hash: int | None = None,
        color_hash: int | None = None,
    ) -> dict:
        """Update official Bungie loadout name/icon/color identifiers."""
        payload = {
            "loadoutIndex": loadout_index,
            "characterId": int(character_id),
            "membershipType": membership_type,
        }
        if name_hash is not None:
            payload["nameHash"] = name_hash
        if icon_hash is not None:
            payload["iconHash"] = icon_hash
        if color_hash is not None:
            payload["colorHash"] = color_hash
        return await self._post_action(
            "Destiny2/Actions/Loadouts/UpdateLoadoutIdentifiers/",
            payload,
            "更新官方配装槽标识",
        )

    async def clear_loadout(
        self,
        loadout_index: int,
        character_id: str,
        membership_type: int,
    ) -> dict:
        """Clear an official Bungie loadout slot."""
        return await self._post_action(
            "Destiny2/Actions/Loadouts/ClearLoadout/",
            {
                "loadoutIndex": loadout_index,
                "characterId": int(character_id),
                "membershipType": membership_type,
            },
            "清空官方配装槽",
        )

    # ── Vendors & Milestones ─────────────────────────────────────────

    async def fetch_vendors(
        self,
        membership_id: str,
        membership_type: int,
        character_id: str,
    ) -> dict:
        """Fetch all vendor inventory for a character.

        Bungie API: GET /Destiny2/{membershipType}/Profile/{destinyMembershipId}/Character/{characterId}/Vendors/
        Components include vendor sales plus item instances/perks/stats/sockets.

        Returns:
            Raw Bungie API response dict.
        """
        token = await self.get_access_token()
        logger.debug(
            "API call: GetVendors(mid=%s, char=%s, comps=%s)",
            membership_id,
            character_id,
            _VENDOR_COMPONENTS_FULL,
        )
        path = f"Destiny2/{membership_type}/Profile/{membership_id}/Character/{character_id}/Vendors/"
        try:
            result = await self.rest.static_request(
                "GET",
                path,
                auth=token,
                params={"components": ",".join(str(c) for c in _VENDOR_COMPONENTS_FULL)},
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取商人库存")
            if not _is_insufficient_privileges(exc):
                raise
            logger.warning(
                "GetVendors full components rejected for mid=%s char=%s; falling back to %s",
                membership_id,
                character_id,
                _VENDOR_COMPONENTS_BASIC,
            )
            try:
                result = await self.rest.static_request(
                    "GET",
                    path,
                    auth=token,
                    params={"components": ",".join(str(c) for c in _VENDOR_COMPONENTS_BASIC)},
                )
            except aiobungie.HTTPError as fallback_exc:
                _raise_bungie_unavailable(fallback_exc, "读取商人基础库存")
                if _is_insufficient_privileges(fallback_exc):
                    raise AuthenticationError(
                        "当前 Bungie OAuth token 缺少商人库存权限 "
                        "ReadDestinyVendorsAndAdvisors，请重新完成 Bungie 授权。"
                    ) from fallback_exc
                raise
        logger.debug("GetVendors returned")
        if not isinstance(result, Mapping):
            logger.error("GetVendors returned unexpected payload type: %s", type(result).__name__)
            raise APIError("读取商人库存", "Bungie 返回了无法解析的数据格式。")
        return dict(result)

    async def fetch_vendor_components(
        self,
        membership_id: str,
        membership_type: int,
        character_id: str,
        vendor_hash: int,
    ) -> dict:
        """Fetch live socket details for one vendor's sale items."""
        token = await self.get_access_token()
        path = (
            f"Destiny2/{membership_type}/Profile/{membership_id}/Character/"
            f"{character_id}/Vendors/{vendor_hash}/"
        )
        try:
            result = await self.rest.static_request(
                "GET",
                path,
                auth=token,
                params={
                    "components": ",".join(
                        str(component) for component in _VENDOR_ITEM_COMPONENTS
                    )
                },
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取商人商品 Perk")
            if _is_insufficient_privileges(exc):
                raise AuthenticationError(
                    "当前 Bungie OAuth token 缺少商人商品详情权限 "
                    "ReadDestinyVendorsAndAdvisors，请重新完成 Bungie 授权。"
                ) from exc
            raise
        logger.debug("GetVendor returned vendor=%s", vendor_hash)
        if not isinstance(result, Mapping):
            logger.error(
                "GetVendor returned unexpected payload type: vendor=%s type=%s",
                vendor_hash,
                type(result).__name__,
            )
            raise APIError("读取商人商品 Perk", "Bungie 返回了无法解析的数据格式。")
        return dict(result)

    async def fetch_milestones(self) -> dict:
        """Fetch current weekly milestones.

        Bungie API: GET /Destiny2/Milestones/
        No OAuth required — public endpoint, only API key.

        Returns:
            Raw Bungie API response dict with milestone data.
        """
        logger.debug("API call: GetPublicMilestones")
        try:
            result = await self.rest.fetch_public_milestones()
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取本周重置")
            raise
        logger.debug("GetPublicMilestones returned")
        return result

    async def fetch_manifest(self, language: str = "en") -> Path:
        """Download the Destiny manifest SQLite database.

        The Bungie manifest is served as a ZIP containing a .content file.
        Manifest metadata and content downloads are API-key-only endpoints;
        they do not require a user OAuth token.
        This method downloads, extracts, and renames it.
        Returns the path to the final SQLite database.
        """
        manifest_dir = config.DESTINY_MANIFEST_PATH
        manifest_dir.mkdir(parents=True, exist_ok=True)

        # Get manifest URL from Bungie
        logger.debug("API call: GetDestinyManifest(language=%s)", language)
        async with httpx.AsyncClient(timeout=600) as http:
            resp = await http.get(
                "https://www.bungie.net/Platform/Destiny2/Manifest/",
                headers={"X-API-Key": config.BUNGIE_API_KEY},
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 503:
                    raise BungieServiceUnavailableError("读取 Destiny Manifest") from exc
                raise
            payload = resp.json()
            if payload.get("ErrorCode", 1) != 1:
                raise ManifestError(
                    f"Bungie manifest request failed: {payload.get('Message', 'Unknown error')}"
                )
            result = payload.get("Response", payload)

        world_url = result.get("mobileWorldContentPaths", {}).get(
            language, result.get("mobileWorldContentPaths", {}).get("en", "")
        )
        if not world_url:
            raise ManifestError("Could not find manifest URL in Bungie API response")

        # Download as zip
        zip_path = manifest_dir / "manifest.zip"
        logger.debug("Downloading Destiny manifest zip: %s", world_url)
        async with httpx.AsyncClient(timeout=600) as http:
            resp = await http.get(f"https://www.bungie.net{world_url}")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 503:
                    raise BungieServiceUnavailableError("下载 Destiny Manifest") from exc
                raise
            zip_path.write_bytes(resp.content)

        # Extract .content file from zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            files = zf.namelist()
            if not files:
                raise ManifestError("Manifest ZIP is empty")
            content_name = files[0]
            zf.extract(content_name, manifest_dir)
            extracted = manifest_dir / content_name

        # Rename to standard name based on language
        if language == "zh-chs":
            dest_name = "destiny_manifest_zh.sqlite3"
        else:
            dest_name = "destiny_manifest.sqlite3"
        dest_path = manifest_dir / dest_name
        if dest_path.exists():
            dest_path.unlink()
        os.rename(str(extracted), str(dest_path))
        zip_path.unlink()

        return dest_path

    # ── Activity & Stats ────────────────────────────────────────────────

    async def get_activity_history(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
        params: dict | None = None,
    ) -> dict:
        """Fetch activity history for a character.

        Bungie API: GET /Destiny2/{membershipType}/Account/{destinyMembershipId}/Character/{characterId}/Stats/Activities/

        Args:
            membership_type: Platform membership type.
            membership_id: Destiny membership ID.
            character_id: Character ID.
            params: Optional query params (count, mode, etc).

        Returns:
            Raw Bungie API response dict.
        """
        logger.debug("API call: GetActivityHistory(mid=%s, char=%s, params=%s)",
                     membership_id, character_id, params)
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Account/{membership_id}/Character/{character_id}/Stats/Activities/",
                params=params,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取活动历史")
            raise
        logger.debug("GetActivityHistory returned")
        return result

    async def get_pgcr(self, activity_id: str) -> dict:
        """Fetch Post-Game Carnage Report for a specific activity.

        Bungie API: GET /Destiny2/Stats/PostGameCarnageReport/{activityId}/
        No OAuth required — public endpoint.

        Args:
            activity_id: The activity instance ID.

        Returns:
            Raw Bungie API response dict.
        """
        logger.debug("API call: GetPGCR(activity_id=%s)", activity_id)
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/Stats/PostGameCarnageReport/{activity_id}/",
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取活动结算报告")
            raise
        logger.debug("GetPGCR returned")
        return result

    async def get_historical_stats(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
    ) -> dict:
        """Fetch lifetime PvE/PvP statistics for a character.

        Bungie API: GET /Destiny2/{membershipType}/Account/{destinyMembershipId}/Character/{characterId}/Stats/

        Args:
            membership_type: Platform membership type.
            membership_id: Destiny membership ID.
            character_id: Character ID.

        Returns:
            Raw Bungie API response dict with allPvE, allPvP sections.
        """
        logger.debug("API call: GetHistoricalStats(mid=%s, char=%s)",
                     membership_id, character_id)
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Account/{membership_id}/Character/{character_id}/Stats/",
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取历史统计")
            raise
        logger.debug("GetHistoricalStats returned")
        return result

    async def get_collectible_node_details(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
        collectible_node_hash: int,
        components: list[int] | None = None,
    ) -> dict:
        """Fetch collectible details for a presentation node."""
        params = {"components": ",".join(str(c) for c in (components or [800]))}
        logger.debug(
            "API call: GetCollectibleNodeDetails(mid=%s char=%s node=%s)",
            membership_id,
            character_id,
            collectible_node_hash,
        )
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Profile/{membership_id}/Character/{character_id}/Collectibles/{collectible_node_hash}/",
                auth=await self.get_access_token(),
                params=params,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取收藏品节点")
            raise
        logger.debug("GetCollectibleNodeDetails returned")
        return result

    async def get_unique_weapon_history(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
    ) -> dict:
        """Fetch per-weapon historical usage for one character."""
        logger.debug(
            "API call: GetUniqueWeaponHistory(mid=%s char=%s)",
            membership_id,
            character_id,
        )
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Account/{membership_id}/Character/{character_id}/Stats/UniqueWeapons/",
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取武器使用历史")
            raise
        logger.debug("GetUniqueWeaponHistory returned")
        return result

    async def get_destiny_aggregate_activity_stats(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
    ) -> dict:
        """Fetch aggregate activity stats for one character."""
        logger.debug(
            "API call: GetDestinyAggregateActivityStats(mid=%s char=%s)",
            membership_id,
            character_id,
        )
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Account/{membership_id}/Character/{character_id}/Stats/AggregateActivityStats/",
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取活动聚合统计")
            raise
        logger.debug("GetDestinyAggregateActivityStats returned")
        return result

    async def get_leaderboards(
        self,
        membership_type: int,
        membership_id: str,
        *,
        maxtop: int | None = None,
        modes: str | None = None,
        statid: str | None = None,
    ) -> dict:
        """Fetch account leaderboards for a Destiny membership."""
        params = {
            key: value
            for key, value in {
                "maxtop": maxtop,
                "modes": modes,
                "statid": statid,
            }.items()
            if value is not None
        }
        logger.debug("API call: GetLeaderboards(mid=%s params=%s)", membership_id, params)
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/{membership_type}/Account/{membership_id}/Stats/Leaderboards/",
                auth=await self.get_access_token(),
                params=params,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取排行榜")
            raise
        logger.debug("GetLeaderboards returned")
        return result

    async def get_leaderboards_for_character(
        self,
        membership_type: int,
        membership_id: str,
        character_id: str,
        *,
        maxtop: int | None = None,
        modes: str | None = None,
        statid: str | None = None,
    ) -> dict:
        """Fetch character leaderboards for a Destiny membership."""
        params = {
            key: value
            for key, value in {
                "maxtop": maxtop,
                "modes": modes,
                "statid": statid,
            }.items()
            if value is not None
        }
        logger.debug(
            "API call: GetLeaderboardsForCharacter(mid=%s char=%s params=%s)",
            membership_id,
            character_id,
            params,
        )
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/Stats/Leaderboards/{membership_type}/{membership_id}/{character_id}/",
                auth=await self.get_access_token(),
                params=params,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取角色排行榜")
            raise
        logger.debug("GetLeaderboardsForCharacter returned")
        return result

    async def get_clan_leaderboards(
        self,
        group_id: str,
        *,
        maxtop: int | None = None,
        modes: str | None = None,
        statid: str | None = None,
    ) -> dict:
        """Fetch clan leaderboards for a Bungie group ID."""
        params = {
            key: value
            for key, value in {
                "maxtop": maxtop,
                "modes": modes,
                "statid": statid,
            }.items()
            if value is not None
        }
        logger.debug("API call: GetClanLeaderboards(group=%s params=%s)", group_id, params)
        try:
            result = await self.rest.static_request(
                "GET",
                f"Destiny2/Stats/Leaderboards/Clans/{group_id}/",
                auth=await self.get_access_token(),
                params=params,
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "读取公会排行榜")
            raise
        logger.debug("GetClanLeaderboards returned")
        return result

    async def search_users(self, display_name_prefix: str) -> dict:
        """Search for Bungie users by display name prefix.

        Bungie API: POST /User/SearchUsers/
        No OAuth required — uses API key only.

        Args:
            display_name_prefix: Partial display name to search for.

        Returns:
            Raw Bungie API response dict.
        """
        logger.debug("API call: SearchUsers(prefix=%s)", display_name_prefix)
        try:
            result = await self.rest.static_request(
                "POST",
                "User/SearchUsers/",
                json={"displayNamePrefix": display_name_prefix},
            )
        except aiobungie.HTTPError as exc:
            _raise_bungie_unavailable(exc, "搜索 Bungie 用户")
            raise
        logger.debug("SearchUsers returned")
        return result
