"""Serial tests against the installed HA decorators, schemas and native writer interface.

Run inside the HA image with COMPONENT_SOURCE pointing to the audited module.
Only synthetic fake config entries are used; no live HA objects or services are called.
"""

import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest

from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.exceptions import Unauthorized
import voluptuous as vol

spec = importlib.util.spec_from_file_location("hnet_watch_maintenance_test_module", os.environ["COMPONENT_SOURCE"])
component = importlib.util.module_from_spec(spec)
spec.loader.exec_module(component)


class FakeEntries:
    def __init__(self):
        self.entries = {}
        self.writes = []
        self.reloads = []
        for agent, (entry_id, subentry_id, domain) in component.AGENTS.items():
            entry = self.entries.setdefault(entry_id, SimpleNamespace(entry_id=entry_id, domain=domain,
                                                                      state=ConfigEntryState.LOADED, subentries={}))
            entry.subentries[subentry_id] = ConfigSubentry(subentry_id=subentry_id, subentry_type="conversation",
                title="Synthetic agent", unique_id=None, data=MappingProxyType({
                    "city": "Synthetic changed city", "region": "Synthetic region", "country": "US",
                    "timezone": "America/New_York", "reasoning_effort": "none",
                    "prompt": "Synthetic old prompt", "chat_model": "fixture-model",
                    "web_search": True, "user_location": True, "nested_setting": {"preserve": [1, 2]},
                    "llm_hass_api": ["assist", "fixture-before", component.WATCH_API, "fixture-after"],
                }))

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)

    def async_update_subentry(self, entry, subentry, *, data):
        self.writes.append((entry.entry_id, subentry.subentry_id, deepcopy(data)))
        entry.subentries[subentry.subentry_id] = replace(subentry, data=MappingProxyType(deepcopy(data)))
        return True

    async def async_reload(self, entry_id):
        self.reloads.append(entry_id)
        return True


class FakeHass:
    def __init__(self):
        self.data = {}
        self.config_entries = FakeEntries()
        self.tasks = []

    def async_create_background_task(self, coro, _name, **_kwargs):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task


class FakeConnection:
    def __init__(self, admin=True):
        self.user = SimpleNamespace(is_admin=admin)
        self.results = []
        self.errors = []

    def send_result(self, _id, result):
        self.results.append(result)

    def send_error(self, _id, code, message):
        self.errors.append((code, message))


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = FakeHass()
        self.connection = FakeConnection()
        self.maintenance = SimpleNamespace(entry_id="synthetic-maintenance-entry")
        await component.async_setup_entry(self.hass, self.maintenance)
        self.temp = tempfile.TemporaryDirectory()
        self.previous_snapshot = component.CITY_SNAPSHOT
        component.CITY_SNAPSHOT = Path(self.temp.name) / "original-city.json"
        os.chmod(self.temp.name, 0o700)
        cities = {agent: {"entry_id": component.AGENTS[agent][0], "subentry_id": component.AGENTS[agent][1],
                          "city": "Synthetic original city"} for agent in ("movie", "rumpus", "phone")}
        component.CITY_SNAPSHOT.write_text(json.dumps({"version": 1, "agents": cities}))
        component.CITY_SNAPSHOT.chmod(0o600)

    async def asyncTearDown(self):
        component.CITY_SNAPSHOT = self.previous_snapshot
        self.temp.cleanup()

    def current(self, agent="movie"):
        _, subentry = component.selected(self.hass, agent)
        return deepcopy(subentry.as_dict())

    async def dispatch(self, handler, payload, connection=None):
        message = handler._ws_schema({"id": 1, "type": handler._ws_command, **payload})
        handler(self.hass, connection or self.connection, message)
        await asyncio.gather(*self.hass.tasks)
        self.hass.tasks.clear()

    def unchanged_except(self, before, after, keys):
        self.assertEqual({k: v for k, v in before.items() if k != "data"},
                         {k: v for k, v in after.items() if k != "data"})
        self.assertEqual({k: v for k, v in before["data"].items() if k not in keys},
                         {k: v for k, v in after["data"].items() if k not in keys})

    async def test_setup_and_status_are_read_only_and_do_not_expose_contents(self):
        await self.dispatch(component.status, {"agent": "movie"})
        self.assertEqual(set(self.connection.results[0]), {"status", "hash"})
        self.assertEqual(self.hass.config_entries.writes, [])
        expected = hashlib.sha256(json.dumps(self.current()["data"], sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        self.assertEqual(self.connection.results[0]["hash"], expected)

    async def test_every_operation_requires_admin(self):
        cases = ((component.status, {"agent": "movie"}),
                 (component.restore_city, {"agent": "movie", "expected_sha256": "0" * 64}),
                 (component.replace_prompt, {"agent": "movie", "prompt": "Synthetic", "expected_sha256": "0" * 64}),
                 (component.set_watch_attachment, {"attached": False, "expected_sha256": "0" * 64}),
                 (component.set_voice_profile, {"agent": "kitchen", "profile": "terra61_westford", "expected_sha256": "0" * 64}))
        for handler, payload in cases:
            with self.subTest(operation=handler._ws_command), self.assertRaises(Unauthorized):
                await self.dispatch(handler, payload, FakeConnection(admin=False))
        self.assertEqual(self.hass.config_entries.writes, [])

    async def test_stale_hash_changes_nothing(self):
        before = self.current()
        await self.dispatch(component.replace_prompt, {"agent": "movie", "prompt": "Synthetic new", "expected_sha256": "0" * 64})
        self.assertEqual(self.connection.errors[0][0], "conflict")
        self.assertEqual(self.current(), before)
        self.assertEqual(self.hass.config_entries.reloads, [])

    async def test_restore_city_changes_only_city_and_reloads_native_entry(self):
        before = self.current()
        await self.dispatch(component.restore_city, {"agent": "movie", "expected_sha256": component.data_hash(before["data"])})
        after = self.current()
        self.unchanged_except(before, after, {"city"})
        self.assertEqual(after["data"]["city"], "Synthetic original city")
        self.assertEqual(len(self.hass.config_entries.writes), 1)
        self.assertEqual(self.hass.config_entries.reloads, [component.AGENTS["movie"][0]])
        self.assertEqual(set(self.connection.results[0]), {"status", "changed", "hash"})

    async def test_prompt_atomic_detach_preserves_city_other_apis_and_settings(self):
        before = self.current("jarvis")
        await self.dispatch(component.replace_prompt, {"agent": "jarvis", "prompt": "Synthetic new",
            "watch_attachment": "detach", "expected_sha256": component.data_hash(before["data"])})
        after = self.current("jarvis")
        self.unchanged_except(before, after, {"prompt", "llm_hass_api"})
        self.assertEqual(after["data"]["llm_hass_api"], ["assist", "fixture-before", "fixture-after"])

    async def test_attachment_control_preserves_every_unrelated_field_and_api(self):
        before = self.current()
        await self.dispatch(component.set_watch_attachment, {"attached": False, "expected_sha256": component.data_hash(before["data"])})
        after = self.current()
        self.unchanged_except(before, after, {"llm_hass_api"})
        self.assertEqual(after["data"]["llm_hass_api"], ["assist", "fixture-before", "fixture-after"])
        self.assertFalse(await component.async_unload_entry(self.hass, self.maintenance))
        await self.dispatch(component.set_watch_attachment, {"attached": True, "expected_sha256": component.data_hash(after["data"])})
        self.assertEqual(self.current(), before, "restore must retain the Watch API's original position")
        self.assertTrue(await component.async_unload_entry(self.hass, self.maintenance))
        registry = self.hass.data.get(component.websocket_api.DOMAIN, {})
        self.assertFalse(any(handler._ws_command in registry for handler in component.HANDLERS))

    async def test_noop_does_not_write_or_reload(self):
        before = self.current()
        await self.dispatch(component.replace_prompt, {"agent": "movie", "prompt": before["data"]["prompt"],
            "expected_sha256": component.data_hash(before["data"])})
        self.assertEqual(self.connection.results[0]["changed"], False)
        self.assertEqual(self.hass.config_entries.writes, [])
        self.assertEqual(self.hass.config_entries.reloads, [])

    async def test_snapshot_bad_permissions_or_identity_fail_closed(self):
        before = self.current()
        for fault in ("permissions", "identity"):
            with self.subTest(fault=fault):
                if fault == "permissions":
                    component.CITY_SNAPSHOT.chmod(0o644)
                else:
                    component.CITY_SNAPSHOT.chmod(0o600)
                    snapshot = json.loads(component.CITY_SNAPSHOT.read_text())
                    snapshot["agents"]["movie"]["subentry_id"] = "synthetic-wrong-identity"
                    component.CITY_SNAPSHOT.write_text(json.dumps(snapshot))
                await self.dispatch(component.restore_city, {"agent": "movie", "expected_sha256": component.data_hash(before["data"])})
                self.assertEqual(self.current(), before)
        self.assertEqual(self.hass.config_entries.writes, [])

    async def test_strict_schemas_refuse_unknown_agents_fields_paths_and_unbounded_input(self):
        cases = ((component.status, {"agent": "unknown"}),
            (component.restore_city, {"agent": "jarvis", "expected_sha256": "0" * 64}),
            (component.restore_city, {"agent": "movie", "path": "/arbitrary", "expected_sha256": "0" * 64}),
            (component.replace_prompt, {"agent": "movie", "prompt": "", "expected_sha256": "0" * 64}),
            (component.replace_prompt, {"agent": "movie", "prompt": "x" * 8001, "expected_sha256": "0" * 64}),
            (component.replace_prompt, {"agent": "movie", "prompt": "Synthetic", "city": "Injected", "expected_sha256": "0" * 64}),
            (component.replace_prompt, {"agent": "movie", "prompt": "Synthetic", "expected_sha256": "0" * 64 + "\n"}),
            (component.set_watch_attachment, {"agent": "phone", "attached": False, "expected_sha256": "0" * 64}),
            (component.replace_prompt, {"agent": "kitchen", "prompt": "Synthetic", "expected_sha256": "0" * 64}),
            (component.set_voice_profile, {"agent": "jarvis", "profile": "terra61_westford", "expected_sha256": "0" * 64}),
            (component.set_voice_profile, {"agent": "kitchen", "profile": "gpt-7", "expected_sha256": "0" * 64}),
            (component.set_voice_profile, {"agent": "kitchen", "profile": "terra61_westford", "city": "Injected",
                                           "expected_sha256": "0" * 64}))
        for handler, payload in cases:
            with self.subTest(operation=handler._ws_command, payload_keys=list(payload)), self.assertRaises(vol.Invalid):
                await self.dispatch(handler, payload)
        self.assertEqual(self.hass.config_entries.writes, [])


    async def test_voice_profile_changes_only_model_effort_and_location(self):
        for agent in component.VOICE_AGENTS:
            with self.subTest(agent=agent):
                before = self.current(agent)
                await self.dispatch(component.set_voice_profile, {"agent": agent, "profile": "terra61_westford",
                    "expected_sha256": component.data_hash(before["data"])})
                after = self.current(agent)
                self.unchanged_except(before, after, set(component.VOICE_PROFILES["terra61_westford"]))
                self.assertEqual({k: after["data"][k] for k in component.VOICE_PROFILES["terra61_westford"]}, {
                    "chat_model": "gpt-6.1-terra", "reasoning_effort": "low", "city": "Westford",
                    "region": "Massachusetts", "country": "US", "timezone": "America/New_York"})
                self.assertEqual(self.hass.config_entries.reloads[-1], component.AGENTS[agent][0])
        self.assertEqual(len(self.hass.config_entries.writes), len(component.VOICE_AGENTS))
        self.assertEqual(self.connection.errors, [])

    async def test_voice_profile_rollback_keeps_the_house_location(self):
        before = self.current("kitchen")
        await self.dispatch(component.set_voice_profile, {"agent": "kitchen", "profile": "terra61_westford",
            "expected_sha256": component.data_hash(before["data"])})
        swapped = self.current("kitchen")
        await self.dispatch(component.set_voice_profile, {"agent": "kitchen", "profile": "terra56_westford",
            "expected_sha256": component.data_hash(swapped["data"])})
        after = self.current("kitchen")
        self.assertEqual((after["data"]["chat_model"], after["data"]["reasoning_effort"], after["data"]["city"]),
                         ("gpt-5.6-terra", "none", "Westford"))
        self.unchanged_except(before, after, set(component.VOICE_PROFILES["terra56_westford"]))

    async def test_voice_profile_refuses_agents_without_web_search_location(self):
        entry_id, subentry_id, _ = component.AGENTS["bedroom"]
        entry = self.hass.config_entries.entries[entry_id]
        old = entry.subentries[subentry_id]
        entry.subentries[subentry_id] = replace(old, data=MappingProxyType({**old.data, "user_location": False}))
        before = self.current("bedroom")
        await self.dispatch(component.set_voice_profile, {"agent": "bedroom", "profile": "terra61_westford",
            "expected_sha256": component.data_hash(before["data"])})
        self.assertEqual(self.connection.errors[0][0], "maintenance_refused")
        self.assertEqual(self.current("bedroom"), before)
        self.assertEqual(self.hass.config_entries.writes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
