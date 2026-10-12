"""Temporary, administrator-only Watch history subentry maintenance."""

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

import voluptuous as vol

DOMAIN = "hnet_watch_maintenance"
WATCH_API = "mcp-01M381GTWER1BG9K4MWG3GDEGR"
CITY_SNAPSHOT = Path("/config/hnet_watch_maintenance_private/original-city.json")
AGENTS = {
    "movie": ("01JK456T3JV6CPBG2ZQ2FS10GE", "01JZ8DWMCRND9599AR8EFJVN0A", "openai_conversation"),
    "rumpus": ("01JK456T3JV6CPBG2ZQ2FS10GE", "01JZ8DWMCR5ZFTVM61SG13HVFR", "openai_conversation"),
    "phone": ("01JBM33KVTM1FHF795G01R2C4X", "01M3FWW3MMFE079D9A7F2NF66F", "openai_conversation"),
    "jarvis": ("01M34TQMBVCKW0BG0KZW5JG5YM", "01M34TQMBVMNR9CJZX892KD8VJ", "llama_cpp"),
}
HASH_SCHEMA = vol.All(str, vol.Length(min=64, max=64), vol.Match(r"^[0-9a-f]{64}$"))


class Refused(Exception):
    """A safe refusal whose message contains no configuration contents."""


def data_hash(data: dict) -> str:
    """Hash the complete data object using the helper's canonical encoding."""
    return hashlib.sha256(json.dumps(dict(data), sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def selected(hass: HomeAssistant, agent: str):
    """Resolve only a known conversation subentry in a loaded expected domain."""
    if agent not in AGENTS:
        raise Refused("Unknown agent")
    entry_id, subentry_id, domain = AGENTS[agent]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != domain or entry.state is not ConfigEntryState.LOADED:
        raise Refused("Conversation entry is not loaded")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise Refused("Known conversation subentry is unavailable")
    return entry, subentry


def original_city(agent: str) -> str:
    """Read only the fixed protected snapshot; never accept a caller path."""
    if agent not in ("movie", "rumpus", "phone"):
        raise Refused("City restoration is limited to the three OpenAI agents")
    parent = CITY_SNAPSHOT.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
            or parent.st_uid != os.geteuid()):
        raise Refused("Private snapshot directory protections are invalid")
    fd = os.open(CITY_SNAPSHOT, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as file:
        protection = os.fstat(file.fileno())
        if (not stat.S_ISREG(protection.st_mode) or stat.S_IMODE(protection.st_mode) != 0o600
                or protection.st_uid != os.geteuid() or protection.st_size > 8192):
            raise Refused("Private snapshot file protections are invalid")
        snapshot = json.load(file)
    if not isinstance(snapshot, dict) or set(snapshot) != {"version", "agents"} or snapshot["version"] != 1:
        raise Refused("Private city snapshot structure is invalid")
    cities = snapshot["agents"]
    if not isinstance(cities, dict) or set(cities) != {"movie", "rumpus", "phone"}:
        raise Refused("Private city snapshot agents are invalid")
    for name, record in cities.items():
        entry_id, subentry_id, _ = AGENTS[name]
        if (not isinstance(record, dict) or set(record) != {"entry_id", "subentry_id", "city"}
                or record["entry_id"] != entry_id or record["subentry_id"] != subentry_id
                or not isinstance(record["city"], str) or not 1 <= len(record["city"]) <= 200):
            raise Refused("Private city snapshot identity or value is invalid")
    return cities[agent]["city"]


def attachment_apis(apis, attached: bool) -> list[str]:
    """Change only the fixed Watch API, preserving all other IDs and order."""
    if (not isinstance(apis, list) or any(not isinstance(api, str) for api in apis)
            or len(set(apis)) != len(apis)):
        raise Refused("Current API list is invalid")
    if attached and WATCH_API not in apis:
        return [*apis, WATCH_API]
    if not attached:
        return [api for api in apis if api != WATCH_API]
    return apis[:]


def target_data(agent: str, current: dict, operation: str, value=None, watch_attachment="keep") -> dict:
    """Construct a new data object using only the operation's allowed fields."""
    if agent not in AGENTS:
        raise Refused("Unknown agent")
    target = deepcopy(current)
    if operation == "restore_city":
        if "city" not in current:
            raise Refused("Current city field is unavailable")
        target["city"] = original_city(agent)
    elif operation == "replace_prompt":
        if not isinstance(value, str) or not 1 <= len(value) <= 8000:
            raise Refused("Prompt must contain 1 to 8000 characters")
        if watch_attachment not in ("keep", "attach", "detach"):
            raise Refused("Unknown Watch attachment operation")
        target["prompt"] = value
        if watch_attachment != "keep":
            target["llm_hass_api"] = attachment_apis(current.get("llm_hass_api"), watch_attachment == "attach")
    elif operation == "set_watch_attachment":
        if agent != "movie" or type(value) is not bool:
            raise Refused("Attachment changes are limited to Movie Room")
        apis = current.get("llm_hass_api")
        if not isinstance(apis, list) or "assist" not in apis:
            raise Refused("Movie Room must retain its Assist API")
        target["llm_hass_api"] = attachment_apis(apis, value)
    else:
        raise Refused("Unknown maintenance operation")
    return target


async def perform(hass: HomeAssistant, connection, message: dict, operation: str) -> None:
    """Compare and mutate on the HA event loop, then reload and verify."""
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        connection.send_error(message["id"], "not_loaded", "Maintenance integration is not loaded")
        return
    agent = "movie" if operation == "set_watch_attachment" else message["agent"]
    try:
        async with runtime["lock"]:
            entry, subentry = selected(hass, agent)
            current = deepcopy(dict(subentry.data))
            current_hash = data_hash(current)
            if operation == "status":
                connection.send_result(message["id"], {"status": "loaded", "hash": current_hash})
                return
            if current_hash != message["expected_sha256"]:
                connection.send_error(message["id"], "conflict", "Current subentry data changed")
                return
            value = message.get("prompt") if operation == "replace_prompt" else message.get("attached")
            target = target_data(agent, current, operation, value, message.get("watch_attachment", "keep"))
            if operation == "set_watch_attachment":
                original_apis = runtime.get("movie_original_apis")
                if not value and WATCH_API in current["llm_hass_api"]:
                    runtime["movie_original_apis"] = current["llm_hass_api"][:]
                elif value and original_apis is not None:
                    if attachment_apis(current["llm_hass_api"], False) != attachment_apis(original_apis, False):
                        raise Refused("Unrelated Movie API attachments changed during the control")
                    # Restore the fixed Watch API to its original position, too.
                    target["llm_hass_api"] = original_apis[:]
            changed = target != current
            if changed:
                # This is the same native writer used by Core config flows.
                hass.config_entries.async_update_subentry(entry, subentry, data=target)
                reloaded = await hass.config_entries.async_reload(entry.entry_id)
                if not reloaded:
                    raise Refused("Conversation reload failed; verify the persisted change")
                _, after = selected(hass, agent)
                if dict(after.data) != target:
                    raise Refused("Concurrent configuration change detected after reload")
                if {k: v for k, v in after.as_dict().items() if k != "data"} != {
                    k: v for k, v in subentry.as_dict().items() if k != "data"
                }:
                    raise Refused("Subentry metadata changed during reload")
            if operation == "set_watch_attachment" and value:
                runtime.pop("movie_original_apis", None)
            connection.send_result(message["id"], {"status": "updated" if changed else "unchanged",
                                                   "changed": changed, "hash": data_hash(target)})
    except (Refused, OSError, ValueError, TypeError, KeyError):
        # Configuration contents, paths, exception values and prompts never leave the handler.
        connection.send_error(message["id"], "maintenance_refused", "Maintenance precondition or verification failed")


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/status", vol.Required("agent"): vol.In(AGENTS)})
@websocket_api.require_admin
@websocket_api.async_response
async def status(hass, connection, message):
    await perform(hass, connection, message, "status")


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/restore_city",
                                  vol.Required("agent"): vol.In(("movie", "rumpus", "phone")),
                                  vol.Required("expected_sha256"): HASH_SCHEMA})
@websocket_api.require_admin
@websocket_api.async_response
async def restore_city(hass, connection, message):
    await perform(hass, connection, message, "restore_city")


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/replace_prompt", vol.Required("agent"): vol.In(AGENTS),
                                  vol.Required("prompt"): vol.All(str, vol.Length(min=1, max=8000)),
                                  vol.Optional("watch_attachment", default="keep"): vol.In(("keep", "attach", "detach")),
                                  vol.Required("expected_sha256"): HASH_SCHEMA})
@websocket_api.require_admin
@websocket_api.async_response
async def replace_prompt(hass, connection, message):
    await perform(hass, connection, message, "replace_prompt")


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/set_watch_attachment", vol.Required("attached"): bool,
                                  vol.Required("expected_sha256"): HASH_SCHEMA})
@websocket_api.require_admin
@websocket_api.async_response
async def set_watch_attachment(hass, connection, message):
    await perform(hass, connection, message, "set_watch_attachment")


HANDLERS = (status, restore_city, replace_prompt, set_watch_attachment)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if DOMAIN in hass.data:
        return False
    hass.data[DOMAIN] = {"entry_id": entry.entry_id, "lock": asyncio.Lock()}
    for handler in HANDLERS:
        websocket_api.async_register_command(hass, handler)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        return True
    async with runtime["lock"]:
        # Do not remove the recovery route while the comparison control is detached.
        try:
            _, movie = selected(hass, "movie")
        except Refused:
            return False
        if WATCH_API not in movie.data.get("llm_hass_api", []):
            return False
        registry = hass.data.get(websocket_api.DOMAIN, {})
        for handler in HANDLERS:
            command = handler._ws_command
            registered = registry.get(command)
            if registered is not None and registered[0] is handler:
                registry.pop(command)
        hass.data.pop(DOMAIN)
    return True
