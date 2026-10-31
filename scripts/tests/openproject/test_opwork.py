"""Offline tests for scripts/openproject/opwork.py against an in-memory fake OpenProject.

Mocked/fixture tests: they prove the helper's logic (claims, conflicts, attribution,
pagination, uncertain create, outage handling), NOT a live cross-harness acceptance run.
No network, kubectl or credentials. Run: python3 -I -B scripts/tests/openproject/test_opwork.py -v
"""
import contextlib
import copy
import errno
import http.client
import http.server
import importlib.util
import io
import itertools
import json
import os
import re
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock
from types import SimpleNamespace
import urllib.error
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "openproject"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


op = load("opwork", "opwork.py")
api = load("agent_api", "agent-api.py")
STATUSES = {"Draft": 23, "Ungroomed": 24, "Needs answer": 25, "Needs approval": 26, "Approved": 27, "In progress": 7, "In review": 28, "Accepted": 29, "Paused": 30}
TYPES = {"Saga": 10, "Epic": 5, "Story": 11}


class Down(Exception):
    pass


class Fake:
    """Just enough of the OpenProject v3 API. `fail` maps 'METHOD substring' -> 'before'|'after':
    raise Down before applying, or after applying (the uncertain case)."""

    def __init__(self):
        self.wps, self.acts, self.rels, self.calls, self.fail, self.next_id, self.next_act = {}, {}, [], [], {}, 100, 1000
        self.deny = set()  # statuses denied regardless of the current state
        self.transitions = {}  # optional current-state -> allowed next statuses
        self.form_checks, self.status_changes = [], []
        self.add(5, "Epic", "Epic five", None)
        self.add(6, "Saga", "Saga six", None)

    def add(self, i, kind, subject, parent, status="Ungroomed", **extra):
        link = lambda base, key, title: {"href": "/api/v3/%s/%d" % (base, key), "title": title}
        self.wps[i] = {"id": i, "subject": subject, "lockVersion": 1, "description": {"raw": ""}, op.OWNER: None, op.SOURCE_KEY: extra.pop("key", None),
                       "_links": {"type": link("types", TYPES[kind], kind), "status": link("statuses", STATUSES[status], status), "parent": {"href": "/api/v3/work_packages/%d" % parent} if parent else {"href": None}}}
        self.wps[i].update(extra)
        self.acts[i] = []
        return self.wps[i]

    def hrefid(self, href):
        return int(href.rsplit("/", 1)[-1])

    def allowed_statuses(self, wp):
        return [n for n in self.transitions.get(wp["_links"]["status"]["title"], STATUSES) if n not in self.deny]

    def request(self, method, path, payload=None):
        self.calls.append((method, path))
        mode = next((m for k, m in self.fail.items() if k.split(" ", 1)[0] == method and k.split(" ", 1)[1] in path), None)
        if mode == "before":
            raise Down("fake outage")
        code, data = self.handle(method, urllib.parse.urlsplit(path), payload)
        if mode == "after":
            raise Down("fake outage after applying")
        return code, data

    def collection(self, rows, query):
        size, page = int(query.get("pageSize", ["20"])[0]), int(query.get("offset", ["1"])[0])
        return 200, {"_type": "Collection", "total": len(rows), "pageSize": size, "offset": page, "_embedded": {"elements": rows[(page - 1) * size:page * size]}}

    def handle(self, method, url, payload):
        path, query = url.path, urllib.parse.parse_qs(url.query)
        if path == "/api/v3/statuses" or path == "/api/v3/types":
            table = STATUSES if path.endswith("statuses") else TYPES
            return 200, {"_embedded": {"elements": [{"id": v, "name": k} for k, v in table.items()]}}
        if path == "/api/v3/users/me":
            return 200, {"login": "openproject-agent", "id": 5}
        if path.startswith("/api/v3/projects/") and path.endswith("/work_packages"):
            if method == "POST":
                return self.create(payload)
            filters = json.loads(query.get("filters", ["[]"])[0])
            return self.collection(self.filter(filters), query)
        m = re.fullmatch(r"/api/v3/work_packages/(\d+)(/activities|/relations|/form|/available_assignees)?", path)
        if m:
            i, sub = int(m.group(1)), m.group(2)
            if i not in self.wps:
                return 404, {"message": "not found"}
            wp = self.wps[i]
            if sub is None and method == "GET":
                return 200, copy.deepcopy(wp)  # independent snapshot, like the real API
            if sub == "/available_assignees":
                return 200, {"_embedded": {"elements": [{"id": 5}, {"id": 6}]}}
            if sub == "/form" and method == "POST":
                if payload["lockVersion"] != wp["lockVersion"]:
                    return 409, {"errorIdentifier": "UpdateConflict"}
                self.form_checks.append((wp["_links"]["status"]["title"], payload["lockVersion"]))
                names = self.allowed_statuses(wp)
                return 200, {"_embedded": {"schema": {"status": {"_embedded": {"allowedValues": [{"name": n} for n in names]}}}}}
            if sub is None and method == "PATCH":
                if payload["lockVersion"] != wp["lockVersion"]:
                    return 409, {"errorIdentifier": "UpdateConflict"}
                target = self.hrefid(payload.get("_links", {}).get("status", {"href": "/0"})["href"])
                if target and target not in [STATUSES[n] for n in self.allowed_statuses(wp)]:
                    return 422, {"message": "no valid transition exists from old to new status"}
                for k, v in payload.items():
                    if k.startswith("customField"):
                        wp[k] = v
                for k, v in payload.get("_links", {}).items():
                    if k == "assignee":
                        wp["_links"][k] = {"href": v["href"]}
                        continue
                    name = next(n for n, s in STATUSES.items() if s == self.hrefid(v["href"]))
                    wp["_links"][k] = {"href": v["href"], "title": name}
                    if k == "status":
                        self.status_changes.append(name)
                wp["lockVersion"] += 1
                return 200, wp
            if sub == "/activities" and method == "GET":
                return 200, {"total": len(self.acts[i]), "_embedded": {"elements": self.acts[i]}}
            if sub == "/activities" and method == "POST":
                self.next_act += 1
                self.acts[i].append({"id": self.next_act, "createdAt": "2026-10-29T00:00:00Z", "comment": payload["comment"], "details": [], "_links": {"user": {"href": "/api/v3/users/5", "title": "openproject-agent"}}})
                return 201, self.acts[i][-1]
            if sub == "/relations" and method == "POST":
                self.rels.append({"type": payload["type"], "_links": {"from": {"href": "/api/v3/work_packages/%d" % i}, "to": payload["_links"]["to"]}})
                return 201, self.rels[-1]
        if path == "/api/v3/relations":
            return 200, {"_embedded": {"elements": self.rels}}
        return 400, {"message": "unsupported in fake: " + path}

    def filter(self, filters):
        rows = sorted(self.wps.values(), key=lambda w: w["id"])
        for f in filters:
            (name, spec), = f.items()
            if name == "status" and spec["operator"] == "o":
                rows = [w for w in rows if w["_links"]["status"]["title"] != "Accepted"]
            elif name == "status" and spec["operator"] == "=":
                rows = [w for w in rows if str(STATUSES[w["_links"]["status"]["title"]]) in spec["values"]]
            elif name == "parent":
                rows = [w for w in rows if w["_links"]["parent"]["href"] and str(self.hrefid(w["_links"]["parent"]["href"])) in spec["values"]]
            elif name == "subjectOrId":
                rows = [w for w in rows if spec["values"][0].lower() in w["subject"].lower()]
        return rows

    def create(self, payload):
        self.next_id += 1
        kind = next(n for n, i in TYPES.items() if i == self.hrefid(payload["_links"]["type"]["href"]))
        status = next(n for n, i in STATUSES.items() if i == self.hrefid(payload["_links"]["status"]["href"]))
        wp = self.add(self.next_id, kind, payload["subject"], self.hrefid(payload["_links"]["parent"]["href"]), status, key=payload[op.SOURCE_KEY])
        wp[op.PROVENANCE] = payload[op.PROVENANCE]
        return 201, wp


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.tracker = op.Tracker(self.fake.request, Down)
        self.state = tempfile.TemporaryDirectory()
        os.environ["OPWORK_STATE_DIR"], os.environ["OPWORK_SESSION"] = self.state.name, "codex-test | worktree w1 | branch agent/w1"
        self.addCleanup(self.state.cleanup)

    def run_cli(self, *argv, up=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = op.main(list(argv), self.tracker if up else None, Down)
        return code, out.getvalue(), err.getvalue()

    def story(self, i=10, **kw):
        return self.fake.add(i, "Story", kw.pop("subject", "Story %d" % i), 5, **kw)

    def posts(self, fragment=""):
        return [c for c in self.fake.calls if c[0] == "POST" and fragment in c[1]]


class SearchTests(Base):
    def test_pagination_reports_totals_and_next_page(self):
        for i in range(10, 55):
            self.story(i)
        code, out, _ = self.run_cli("search", "--limit", "20", "--page", "3")
        self.assertEqual(code, 0)
        self.assertIn("-- 7 of 47 (page 3, 20 per page)", out)  # 45 stories + epic + saga
        self.assertEqual(self.run_cli("search", "--limit", "20", "--page", "1")[1].count("\n"), 21)
        self.assertIn("use --page 2 for more", self.run_cli("search", "--limit", "20")[1])

    def test_status_and_closed_filters(self):
        self.story(10, status="Accepted")
        self.story(11, status="Paused")
        self.assertNotIn("#10 ", self.run_cli("search")[1])
        self.assertIn("#10 ", self.run_cli("search", "--all")[1])
        self.assertEqual(self.run_cli("search", "--status", "Paused")[1].count("[Paused]"), 1)

    def test_source_key_and_owner_match_exactly_client_side(self):
        self.story(10, key="k:one")
        self.story(11, key="k:one-more")
        out = self.run_cli("search", "--source-key", "k:one")[1]
        self.assertIn("#10 ", out)
        self.assertNotIn("#11 ", out)


class ClaimTests(Base):
    def test_claim_records_session_owner_status_and_attributed_note_with_readback(self):
        self.story()
        code, out, _ = self.run_cli("claim", "10", "--note", "starting")
        self.assertEqual(code, 0, out)
        wp = self.fake.wps[10]
        self.assertEqual(wp[op.OWNER], "codex-test | worktree w1 | branch agent/w1")
        self.assertEqual(wp["_links"]["status"]["title"], "In progress")
        self.assertEqual(wp["lockVersion"], 2)
        note = self.fake.acts[10][0]["comment"]["raw"]
        self.assertTrue(note.startswith("**[codex-test | worktree w1 | branch agent/w1]** Claimed. starting"))
        self.assertIn("(opwork:note=", note)

    def test_other_owner_is_not_overwritten_without_force(self):
        self.story(10, **{op.OWNER: "claude | worktree other | branch b"})
        code, _, err = self.run_cli("claim", "10")
        self.assertEqual(code, 3)
        self.assertIn("owned by <claude | worktree other", err)
        self.assertEqual(self.fake.wps[10][op.OWNER], "claude | worktree other | branch b")
        self.assertEqual(self.fake.wps[10]["lockVersion"], 1)
        self.assertEqual(self.run_cli("claim", "10", "--force")[0], 0)

    def test_stale_lock_is_a_conflict_that_overwrites_nothing(self):
        self.story()
        self.fake.wps[10]["lockVersion"] = 4  # someone else saved after the caller's read
        code, _, err = self.run_cli("claim", "10", "--expect-lock", "1")
        self.assertEqual(code, 3)
        self.assertIn("CONFLICT", err)
        self.assertIn("lock 1 -> 4", err)
        self.assertIsNone(self.fake.wps[10][op.OWNER])

    def test_change_between_read_and_write_is_a_conflict(self):
        self.story()
        original = self.fake.handle
        def racing(method, url, payload):
            if method == "PATCH":  # another agent lands first
                self.fake.wps[10]["lockVersion"] += 1
            return original(method, url, payload)
        self.fake.handle = racing
        code, _, err = self.run_cli("claim", "10")
        self.assertEqual(code, 3)
        self.assertIn("Not overwritten", err)

    def test_patch_that_landed_before_the_connection_dropped_is_confirmed_by_readback(self):
        self.story()
        self.fake.fail["PATCH /api/v3/work_packages"] = "after"
        code, out, _ = self.run_cli("claim", "10")
        self.assertEqual(code, 0)
        self.assertIn("claimed (read back)", out)
        self.assertEqual(self.fake.wps[10]["lockVersion"], 2)  # applied once

    def test_claim_assignee_me_is_set_and_read_back_never_tom_by_default(self):
        self.story()
        self.assertEqual(self.run_cli("claim", "10")[0], 0)
        self.assertIsNone(self.fake.wps[10]["_links"].get("assignee", {}).get("href"))  # opt-in only
        self.story(11)
        code, out, _ = self.run_cli("claim", "11", "--assignee", "me")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fake.wps[11]["_links"]["assignee"]["href"], "/api/v3/users/5")
        self.assertEqual(self.fake.wps[11][op.OWNER], os.environ["OPWORK_SESSION"])  # session owner kept

    def test_claim_assignee_must_be_an_available_principal(self):
        self.story()
        code, _, err = self.run_cli("claim", "10", "--assignee", "99")
        self.assertEqual(code, 1)
        self.assertIn("not an available assignee", err)
        self.assertIsNone(self.fake.wps[10][op.OWNER])

    def test_paused_is_a_hold_too(self):
        self.story(12, status="Paused")
        self.assertEqual(self.run_cli("claim", "12")[0], 3)

    def test_holds_and_closed_items_are_refused(self):
        self.story(10, status="Needs approval")
        self.story(11, status="Accepted")
        for i in (10, 11):
            self.assertEqual(self.run_cli("claim", str(i))[0], 3)
        self.assertEqual(self.run_cli("claim", "10", "--force")[0], 0)

    def test_write_not_visible_on_readback_is_uncertain_not_success(self):
        self.story()
        self.fake.handle_real = self.fake.handle
        self.fake.handle = lambda m, u, p: (200, {}) if m == "PATCH" else self.fake.handle_real(m, u, p)
        code, _, err = self.run_cli("claim", "10")
        self.assertEqual(code, 6)
        self.assertIn("UNCERTAIN", err)


class LifecycleTests(Base):
    def test_complete_posts_evidence_then_accepts_and_is_idempotent(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        code, out, _ = self.run_cli("complete", "10", "--evidence", "tests green")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "Accepted")
        self.assertIn("Completion evidence: tests green", self.fake.acts[10][0]["comment"]["raw"])
        self.assertIn("already Accepted", self.run_cli("complete", "10", "--evidence", "x")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)

    def test_complete_by_non_owner_and_during_hold_is_refused_before_any_write(self):
        self.story(10, status="In review", **{op.OWNER: "someone else"})
        for i, status in enumerate(op.HOLDS, 11):
            self.story(i, status=status)
        for i in range(10, 11 + len(op.HOLDS)):
            self.assertEqual(self.run_cli("complete", str(i), "--evidence", "x")[0], 3)
            self.assertEqual(self.fake.acts[i], [])
            self.assertEqual(self.fake.wps[i]["lockVersion"], 1)
        self.assertFalse(self.posts())
        self.assertEqual(self.fake.status_changes, [])

    def complete_through_review(self, status, *extra):
        self.story(10, status=status, **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.fake.transitions = {status: ["In review"], "In review": ["Accepted"]}
        code, out, err = self.run_cli("complete", "10", "--evidence", "tests green", *extra)
        self.assertEqual(code, 0, err)
        self.assertIn("completed (read back)", out)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "Accepted")
        self.assertEqual(self.fake.status_changes, ["In review", "Accepted"])
        self.assertEqual(self.fake.form_checks, [(status, 1), ("In review", 2)])
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("evidence: tests green", self.fake.acts[10][0]["comment"]["raw"])

    def test_complete_from_ungroomed_rechecks_workflow_and_accepts_in_one_call(self):
        self.complete_through_review("Ungroomed")

    def test_complete_through_review_uses_new_lock_after_valid_expected_lock(self):
        self.complete_through_review("Ungroomed", "--expect-lock", "1")

    def test_complete_through_review_refuses_stale_expected_lock(self):
        self.story(10, lockVersion=4)
        self.fake.transitions = {"Ungroomed": ["In review"], "In review": ["Accepted"]}
        code, _, err = self.run_cli("complete", "10", "--expect-lock", "1", "--evidence", "ev")
        self.assertEqual(code, 3)
        self.assertIn("CONFLICT", err)
        self.assertIn("lock 1 -> 4", err)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "Ungroomed")
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertEqual(self.fake.status_changes, [])

    def test_complete_from_approval_hold_rechecks_workflow_with_explicit_force(self):
        self.complete_through_review("Needs approval", "--force")

    def test_complete_force_overrides_owner_guard_without_changing_owner(self):
        self.story(10, status="In review", **{op.OWNER: "someone else"})
        self.fake.transitions = {"In review": ["Accepted"]}
        code, _, err = self.run_cli("complete", "10", "--force", "--evidence", "ev")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.wps[10][op.OWNER], "someone else")
        self.assertEqual(self.fake.status_changes, ["Accepted"])

    def test_complete_denied_after_review_reports_current_allowed_statuses(self):
        self.story(10)
        self.fake.transitions = {"Ungroomed": ["In review"], "In review": ["In review", "Needs approval"]}
        code, _, err = self.run_cli("complete", "10", "--evidence", "tests green")
        self.assertEqual(code, 7)
        self.assertIn("EVIDENCE RECORDED, NOT ACCEPTED", err)
        self.assertIn("current state [In review]", err)
        self.assertIn("Allowed next statuses: In review, Needs approval.", err)
        self.assertIn("An authorized accepter may be needed", err)
        self.assertNotIn("this role", err)
        self.assertEqual(self.fake.form_checks, [("Ungroomed", 1), ("In review", 2)])
        self.assertEqual(self.fake.status_changes, ["In review"])
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("evidence: tests green", self.fake.acts[10][0]["comment"]["raw"])

    def test_complete_form_recheck_failure_reports_evidence_and_review_state(self):
        self.story(10)
        self.fake.transitions = {"Ungroomed": ["In review"], "In review": ["Accepted"]}
        original = self.fake.handle
        def failing_recheck(method, url, payload):
            result = original(method, url, payload)
            if method == "POST" and url.path.endswith("/form") and self.fake.status_changes == ["In review"]:
                return 500, {"message": "form unavailable"}
            return result
        self.fake.handle = failing_recheck
        code, out, err = self.run_cli("complete", "10", "--evidence", "tests green")
        self.assertEqual(code, 7)
        self.assertEqual(out, "")
        self.assertIn("EVIDENCE RECORDED, NOT ACCEPTED", err)
        self.assertIn("#10 is [In review]; form re-check failed", err)
        self.assertIn("HTTP 500", err)
        self.assertIn("Actual state after read back: " + op.line(self.fake.wps[10]), err)
        self.assertIn("Do not re-run complete; check with `opwork show 10`", err)
        self.assertEqual(self.fake.form_checks, [("Ungroomed", 1), ("In review", 2)])
        self.assertEqual(self.fake.status_changes, ["In review"])
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("Ready for acceptance; evidence: tests green", self.fake.acts[10][0]["comment"]["raw"])

    def test_complete_acceptance_conflict_reports_evidence_and_review_transition(self):
        self.story(10)
        self.fake.transitions = {"Ungroomed": ["In review"], "In review": ["Accepted"]}
        original = self.fake.handle
        def racing_recheck(method, url, payload):
            result = original(method, url, payload)
            if method == "POST" and url.path.endswith("/form") and self.fake.status_changes == ["In review"]:
                self.fake.wps[10]["lockVersion"] += 1
            return result
        self.fake.handle = racing_recheck
        code, out, err = self.run_cli("complete", "10", "--evidence", "tests green")
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("EVIDENCE RECORDED", err)
        self.assertIn("#10 was in [In review] after read back; acceptance conflicted", err)
        self.assertIn("CONFLICT", err)
        self.assertIn("Not overwritten. Now: " + op.line(self.fake.wps[10]), err)
        self.assertIn("lock 2 -> 3", err)
        self.assertIn("Do not re-run complete; check with `opwork show 10`", err)
        self.assertEqual(self.fake.status_changes, ["In review"])
        self.assertEqual(len(self.fake.acts[10]), 1)

    def test_complete_without_transition_displays_fresh_lock_despite_expected_lock(self):
        original = self.fake.handle
        for status in ("Ungroomed", "In review"):
            with self.subTest(status=status):
                self.fake.handle = original
                self.story(10, status=status)
                self.fake.transitions = {status: []}
                self.concurrent_change_during_note()
                code, _, err = self.run_cli("complete", "10", "--expect-lock", "1", "--evidence", "ev")
                self.assertEqual(code, 7)
                self.assertIn("Actual state after read back: " + op.line(self.fake.wps[10]), err)
                self.assertEqual(self.fake.wps[10]["lockVersion"], 2)
                self.assertEqual(self.fake.status_changes, [])
                self.assertEqual(len(self.fake.acts[10]), 1)

    def test_complete_force_does_not_override_workflow_with_no_allowed_statuses(self):
        self.story(10, status="In review")
        self.fake.transitions = {"In review": []}
        code, _, err = self.run_cli("complete", "10", "--force", "--evidence", "ev")
        self.assertEqual(code, 7)
        self.assertIn("current state [In review]", err)
        self.assertIn("Allowed next statuses: (none)", err)
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertEqual(self.fake.status_changes, [])

    def test_status_note_is_only_posted_after_the_change_took_effect(self):
        self.story()
        self.fake.deny.add("Paused")
        code, _, err = self.run_cli("status", "10", "Paused", "--note", "pausing")
        self.assertEqual(code, 1)
        self.assertIn("no valid transition", err)
        self.assertEqual(self.fake.acts[10], [])  # no misleading "Status -> Paused" comment
        self.assertEqual(self.run_cli("status", "10", "In review", "--note", "PR 1")[0], 0)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "In review")

    def concurrent_change_during_note(self, **change):
        original = self.fake.handle
        def racing(method, url, payload):
            result = original(method, url, payload)
            if method == "POST" and url.path.endswith("/activities"):  # someone else saves right after our note
                wp = self.fake.wps[10]
                wp["lockVersion"] += 1
                wp[op.OWNER] = change.get("owner", wp[op.OWNER])
                if "status" in change:
                    wp["_links"]["status"] = {"href": "/api/v3/statuses/%d" % STATUSES[change["status"]], "title": change["status"]}
            return result
        self.fake.handle = racing

    def test_complete_refuses_when_owner_or_hold_changes_during_the_command(self):
        for change in ({"owner": "other session", "status": "Paused"}, {"status": "Needs approval"}):
            self.setUp()
            self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
            self.concurrent_change_during_note(**change)
            code, out, err = self.run_cli("complete", "10", "--evidence", "ev")
            self.assertEqual(code, 3, (change, out, err))
            self.assertIn("changed while this command ran", err)
            self.assertNotEqual(self.fake.wps[10]["_links"]["status"]["title"], "Accepted")

    def test_complete_adopts_a_bumped_lock_when_only_the_lock_moved(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.concurrent_change_during_note()
        self.assertEqual(self.run_cli("complete", "10", "--evidence", "ev")[0], 0)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "Accepted")

    def test_denied_branch_also_refuses_a_concurrent_pause(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.fake.deny.add("Accepted")
        self.concurrent_change_during_note(status="Paused")
        self.assertEqual(self.run_cli("complete", "10", "--evidence", "ev")[0], 3)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "Paused")

    def test_evidence_survives_a_status_failure_and_rerun_does_not_hide_it(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.fake.fail["PATCH /api/v3/work_packages"] = "before"
        self.assertEqual(self.run_cli("complete", "10", "--evidence", "ev")[0], 5)
        self.assertEqual(len(self.fake.acts[10]), 1)  # evidence first, so an outage cannot lose it
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "In progress")

    def test_complete_when_workflow_denies_accepted_records_evidence_and_says_so(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.fake.deny.add("Accepted")
        code, _, err = self.run_cli("complete", "10", "--evidence", "tests green")
        self.assertEqual(code, 7)
        self.assertIn("NOT ACCEPTED", err)
        self.assertEqual(self.fake.wps[10]["_links"]["status"]["title"], "In review")
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("Ready for acceptance; evidence: tests green", self.fake.acts[10][0]["comment"]["raw"])
        self.assertNotIn("Completed", self.fake.acts[10][0]["comment"]["raw"])

    def test_unknown_status_names_the_known_ones(self):
        self.story()
        code, _, err = self.run_cli("status", "10", "Bogus")
        self.assertEqual(code, 1)
        self.assertIn("Known:", err)

    def test_comment_from_stdin_and_attribution(self):
        self.story()
        import sys
        old, sys.stdin = sys.stdin, io.StringIO("progress from stdin\n")
        try:
            self.assertEqual(self.run_cli("comment", "10", "-")[0], 0)
        finally:
            sys.stdin = old
        self.assertIn("progress from stdin", self.fake.acts[10][0]["comment"]["raw"])

    def test_long_notes_are_marked_truncated_by_default_and_complete_with_full(self):
        self.story()
        long = "x" * 700 + "END"
        self.run_cli("comment", "10", long)
        short = self.run_cli("show", "10", "--history")[1]
        self.assertIn("truncated", short)
        self.assertNotIn("END", short)
        self.assertIn(long, self.run_cli("show", "10", "--history", "--full")[1])

    def test_complete_denied_reports_the_actual_state_even_when_in_review_is_not_allowed(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        self.fake.deny.update({"Accepted", "In review"})
        code, _, err = self.run_cli("complete", "10", "--evidence", "ev")
        self.assertEqual(code, 7)
        self.assertIn("[In progress]", err)
        self.assertNotIn("left [In review]", err)

    def test_show_history_includes_comments_and_session(self):
        self.story()
        self.run_cli("comment", "10", "hello")
        out = self.run_cli("show", "10", "--history", "--relations")[1]
        self.assertIn("hello", out)
        self.assertIn("openproject-agent", out)


class CreateTests(Base):
    args = ("create", "--epic", "5", "--origin", "10", "--source-key", "followup:10:cache", "--subject", "Add retry cache layer")

    def test_creates_story_under_epic_ungroomed_with_relation_to_origin(self):
        self.story()
        code, out, err = self.run_cli(*self.args)
        self.assertEqual(code, 0, err)
        made = [w for w in self.fake.wps.values() if w[op.SOURCE_KEY] == "followup:10:cache"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["_links"]["status"]["title"], "Ungroomed")
        self.assertEqual(self.fake.hrefid(made[0]["_links"]["parent"]["href"]), 5)
        self.assertIn("codex-test | worktree w1", made[0][op.PROVENANCE]["raw"])
        self.assertEqual(self.fake.rels[0]["type"], "relates")
        self.assertIn("related to #10", out)

    def test_existing_source_key_is_reused_never_created_twice(self):
        self.story()
        self.run_cli(*self.args)
        code, out, _ = self.run_cli(*self.args)
        self.assertEqual(code, 0)
        self.assertIn("REUSED", out)
        self.assertEqual(len(self.posts("/projects/")), 1)

    def test_similar_subject_under_epic_needs_a_decision(self):
        self.story(20, subject="Add retry cache layer for tracker")
        code, _, err = self.run_cli(*self.args)
        self.assertEqual(code, 4)
        self.assertIn("POSSIBLE DUPLICATE", err)
        self.assertEqual(self.posts("/projects/"), [])
        self.assertEqual(self.run_cli(*self.args, "--confirm-new")[0], 0)

    def test_grammar_story_must_sit_under_an_epic(self):
        self.story()
        code, _, err = self.run_cli("create", "--epic", "6", "--source-key", "k", "--subject", "x")
        self.assertEqual((code, "goes under a Epic" in err), (1, True))

    def test_rerun_after_reuse_completes_a_lost_origin_relation(self):
        self.story()
        self.run_cli(*self.args)
        self.fake.rels.clear()  # the relation step was lost on the first run
        out = self.run_cli(*self.args)[1]
        self.assertIn("REUSED", out)
        self.assertIn("related to #10", out)
        self.assertEqual(len(self.fake.rels), 1)

    def test_genuinely_opposite_relation_is_not_mistaken_for_present(self):
        self.story(11)
        self.story()
        self.fake.rels.append({"type": "precedes", "_links": {"from": {"href": "/api/v3/work_packages/10"}, "to": {"href": "/api/v3/work_packages/11"}}})
        # 10 precedes 11 means 11 follows 10; "10 follows 11" is the opposite and must be created
        code, out, _ = self.run_cli("relate", "10", "11", "--type", "follows")
        self.assertEqual((code, "created relation" in out, len(self.fake.rels)), (0, True, 2))

    def test_relation_stored_in_canonical_orientation_is_still_confirmed(self):
        self.story(11)
        self.story()
        self.fake.rels.append({"type": "precedes", "_links": {"from": {"href": "/api/v3/work_packages/10"}, "to": {"href": "/api/v3/work_packages/11"}}})
        code, out, _ = self.run_cli("relate", "11", "10", "--type", "follows")
        self.assertEqual((code, "already present" in out, len(self.fake.rels)), (0, True, 1))

    def test_create_lost_after_applying_is_found_by_readback_and_never_retried(self):
        self.story()
        self.fake.fail["POST /api/v3/projects"] = "after"
        code, out, _ = self.run_cli(*self.args)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.posts("/projects/")), 1)
        self.assertEqual(len([w for w in self.fake.wps.values() if w[op.SOURCE_KEY] == "followup:10:cache"]), 1)

    def test_create_lost_before_applying_saves_intent_and_does_not_retry(self):
        self.story()
        self.fake.fail["POST /api/v3/projects"] = "before"
        code, _, err = self.run_cli(*self.args)
        self.assertEqual(code, 5)
        self.assertIn("UNCONFIRMED", err)
        self.assertNotIn("nothing was recorded", err)
        self.assertEqual(len(self.posts("/projects/")), 1)
        self.assertIn("intent", self.run_cli("pending")[1])

    def test_existing_duplicates_of_a_key_are_reused_not_created_over(self):
        self.story()
        self.fake.add(30, "Story", "unrelated", 5, key="followup:10:cache")
        self.fake.add(31, "Story", "unrelated two", 5, key="followup:10:cache")
        code, out, _ = self.run_cli("create", "--epic", "5", "--source-key", "followup:10:cache", "--subject", "x")
        self.assertEqual((code, "#30" in out, "#31" in out, self.posts("/projects/")), (0, True, True, []))


class CheckinTests(Base):
    def test_structured_checkpoint_and_bounded_view(self):
        self.story(10, status="In progress", **{op.OWNER: os.environ["OPWORK_SESSION"]})
        args = ("checkpoint-update", "10", "--next-action", "verify", "--check-after", "2020-01-01T00:00:00Z", "--evidence", "run", "--missing-ack", "--deployed")
        self.assertEqual(self.run_cli(*args)[0], 0)
        code, out, _ = self.run_cli("checkins", "--owner", "codex-test", "--status", "In progress", "-o", "json")
        item = json.loads(out)["items"][0]
        self.assertEqual(code, 0)
        self.assertEqual(item["reasons"], ["due", "missing_ack", "deployed_not_accepted"])
        self.assertEqual(item["comment_source"]["author"], "openproject-agent")
        self.assertTrue(item["history_complete"])
        self.assertEqual(item["last_verified"], "Unknown")

    def test_actor_href_without_title_and_invalid_actor(self):
        self.story(10)
        self.assertEqual(self.run_cli("checkpoint-update", "10", "--next-action", "verify", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "run")[0], 0)
        act = self.fake.acts[10][0]
        act["_links"]["user"] = {"href": "/api/v3/users/5"}
        def item():
            return next(x for x in json.loads(self.run_cli("checkins", "-o", "json")[1])["items"] if x["id"] == 10)
        self.assertEqual((item()["metadata"], item()["comment_source"]["author"]), ("present", "user#5"))
        for user in ({}, {"title": "name"}, {"href": "/api/v3/users/not-a-number"}, None):
            act["_links"]["user"] = user
            self.assertEqual((item()["metadata"], item()["next_action"]), ("malformed", "Unknown"))


    def test_bad_time_guard_and_incomplete_metadata(self):
        self.story(10, status="Paused")
        args = ("checkpoint-update", "10", "--next-action", "x", "--check-after", "2020-01-01", "--evidence", "x")
        self.assertEqual(self.run_cli(*args)[0], 3)
        self.fake.wps[10]["_links"]["status"]["title"] = "In progress"
        self.assertEqual(self.run_cli(*args)[0], 1)
        code, out, _ = self.run_cli("checkins", "--owner", "missing", "-o", "json")
        self.assertEqual(json.loads(out)["items"], [])
        self.assertEqual(self.run_cli("checkins", "--limit", "101", "-o", "json")[0], 1)

    def test_uncertain_checkpoint_is_queued_and_not_replayed_on_partial_history(self):
        self.story(10)
        self.fake.fail["POST /activities"] = "after"
        args = ("checkpoint-update", "10", "--next-action", "x", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.assertEqual(self.run_cli(*args)[0], 5)
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.fake.fail.clear()
        self.assertIn("flushed", self.run_cli("flush")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)

    def test_malformed_metadata_is_truthful(self):
        self.story(10)
        self.fake.acts[10].append({"id": 1, "createdAt": "2020-01-01T00:00:00Z", "comment": {"raw": "opwork:checkin={broken"}})
        out = self.run_cli("checkins", "-o", "json")[1]
        item = next(x for x in json.loads(out)["items"] if x["id"] == 10)
        self.assertEqual(item["metadata"], "malformed")
        self.assertEqual(item["next_action"], "Unknown")


    def test_partial_history_hides_visible_old_values(self):
        self.story(10)
        self.run_cli("checkpoint-update", "10", "--next-action", "old", "--check-after", "2020-01-01T00:00:00Z", "--evidence", "old")
        original = self.fake.handle
        def partial(method, url, payload):
            code, data = original(method, url, payload)
            if method == "GET" and url.path.endswith("/activities"):
                data["total"] += 1
            return code, data
        self.fake.handle = partial
        item = next(x for x in json.loads(self.run_cli("checkins", "-o", "json")[1])["items"] if x["id"] == 10)
        self.assertEqual((item["next_action"], item["reasons"], item["metadata"]), ("Unknown", ["unknown"], "unknown"))

    def test_malformed_collections_fail_and_page_filter_is_explicit(self):
        self.story(10, **{op.OWNER: "target"})
        out = json.loads(self.run_cli("checkins", "--owner", "target", "--limit", "1", "-o", "json")[1])
        self.assertEqual((out["items"], out["owner_filter_scope"], out["has_more"]), ([], "selected page", True))
        original = self.fake.handle
        def broken(method, url, payload):
            code, data = original(method, url, payload)
            if url.path.endswith("/activities") and method == "GET":
                data.pop("total")
            return code, data
        self.fake.handle = broken
        self.assertEqual(self.run_cli("checkins", "-o", "json")[0], 1)

    def test_malformed_envelope_and_optional_timestamp(self):
        self.story(10)
        self.run_cli("checkpoint-update", "10", "--next-action", "x", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        act = self.fake.acts[10][0]
        act["comment"]["raw"] = act["comment"]["raw"].replace('"last_verified":null', '"last_verified":"nonsense"')
        item = next(x for x in json.loads(self.run_cli("checkins", "-o", "json")[1])["items"] if x["id"] == 10)
        self.assertEqual((item["metadata"], item["last_verified"]), ("malformed", "Unknown"))

    def test_post_readback_then_refresh_conflict_reports_recorded(self):
        self.story(10)
        original = self.fake.handle
        def changed(method, url, payload):
            result = original(method, url, payload)
            if method == "POST" and url.path.endswith("/activities"):
                self.fake.wps[10][op.OWNER] = "other"
            return result
        self.fake.handle = changed
        code, _, err = self.run_cli("checkpoint-update", "10", "--next-action", "x", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.assertEqual(code, 6)
        self.assertIn("recorded", err)
        self.assertIn("Do not blindly retry", err)


    def test_same_timestamp_orders_numeric_activity_ids(self):
        self.story(10)
        args = ("checkpoint-update", "10", "--next-action", "old", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.run_cli(*args)
        self.fake.acts[10][0]["id"] = 9
        self.run_cli("checkpoint-update", "10", "--next-action", "new", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.fake.acts[10][1]["id"] = 10
        item = next(x for x in json.loads(self.run_cli("checkins", "-o", "json")[1])["items"] if x["id"] == 10)
        self.assertEqual(item["next_action"], "new")

    def test_empty_checkpoint_fields_refused_before_post(self):
        self.story(10)
        for action, evidence, handoff in ((" ", "x", "ok"), ("x", "", "ok"), ("x", "x", " ")):
            code, _, _ = self.run_cli("checkpoint-update", "10", "--next-action", action, "--check-after", "2030-01-01T00:00:00Z", "--evidence", evidence, "--handoff", handoff)
            self.assertEqual(code, 1)
        for value in ("", " "):
            code, _, _ = self.run_cli("checkpoint-update", "10", "--next-action", "x", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x", "--last-verified", value)
            self.assertEqual(code, 1)
        self.assertEqual(self.posts("/activities"), [])


    def test_uncertain_readback_reports_queued_token_and_dedupes(self):
        self.story(10)
        original = self.fake.handle
        def hide_readback(method, url, payload):
            code, data = original(method, url, payload)
            if method == "GET" and url.path.endswith("/activities"):
                return code, {"total": 0, "_embedded": {"elements": []}}
            return code, data
        self.fake.handle = hide_readback
        code, _, err = self.run_cli("checkpoint-update", "10", "--next-action", "x", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.assertEqual(code, 6)
        self.assertIn("Queued locally:", err)
        self.assertIn("Do not rerun checkpoint-update", err)
        self.assertIn("same token", err)
        self.fake.handle = original
        self.assertIn("flushed", self.run_cli("flush")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)


    def test_deferred_checkpoint_does_not_supersede_newer_checkpoint(self):
        self.story(10)
        args = ("checkpoint-update", "10", "--next-action", "old", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")
        self.fake.fail["POST /activities"] = "before"
        self.assertEqual(self.run_cli(*args)[0], 5)
        self.fake.fail.clear()
        self.assertEqual(self.run_cli("checkpoint-update", "10", "--next-action", "new", "--check-after", "2030-01-01T00:00:00Z", "--evidence", "x")[0], 0)
        self.fake.acts[10][0]["createdAt"] = "2099-01-01T00:00:00Z"
        self.assertIn("superseded", self.run_cli("flush")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("old", self.run_cli("pending")[1])


class LinkTests(Base):
    BASE = "https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/"

    def setUp(self):
        super().setUp()
        saved = os.environ.pop("OPWORK_BASE_URL", None)
        self.addCleanup(lambda: os.environ.pop("OPWORK_BASE_URL", None) if saved is None else os.environ.__setitem__("OPWORK_BASE_URL", saved))

    def test_one_line_form_adds_only_the_work_package_link(self):
        self.story(10, subject="Ten")
        self.story(11, subject="Eleven", status="In progress")
        out = self.run_cli("search")[1]
        # Everything before and after the link is the existing line, unchanged.
        self.assertIn("#10 Story [Ungroomed] parent=#5 lock=1 %s10 - Ten\n" % self.BASE, out)
        self.assertIn("#11 Story [In progress] parent=#5 lock=1 %s11 - Eleven\n" % self.BASE, out)
        self.assertNotIn(self.BASE + "5/", out)
        self.assertEqual(op.line(self.fake.wps[10]).replace(self.BASE + "10 ", ""), "#10 Story [Ungroomed] parent=#5 lock=1 - Ten")

    def test_show_header_has_the_link_and_the_rest_is_unchanged(self):
        self.story(10)
        code, out, _ = self.run_cli("show", "10")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "#10 Story [Ungroomed] parent=#5 lock=1 %s10 - Story 10" % self.BASE)
        self.assertEqual(out.count("http"), 1)
        self.assertIn("  source-key: ", out)

    def test_create_result_links_the_new_item_by_its_id(self):
        self.story()
        code, out, err = self.run_cli(*CreateTests.args)
        self.assertEqual(code, 0, err)
        made = next(w for w in self.fake.wps.values() if w[op.SOURCE_KEY] == "followup:10:cache")
        self.assertIn("created (read back): #%d Story [Ungroomed] parent=#5 lock=1 %s%d - Add retry cache layer" % (made["id"], self.BASE, made["id"]), out)
        self.assertNotIn(self.BASE + "10 ", out)  # the origin is only named, not re-printed
        reused = self.run_cli(*CreateTests.args)[1]
        self.assertIn("REUSED, not created: #%d " % made["id"], reused)
        self.assertIn("%s%d - " % (self.BASE, made["id"]), reused)

    def test_claim_and_status_lines_carry_the_link(self):
        self.story()
        self.assertIn(self.BASE + "10 ", self.run_cli("claim", "10")[1])
        self.assertIn(self.BASE + "10 ", self.run_cli("status", "10", "In review")[1])

    def test_base_can_be_overridden_and_project_follows_the_flag(self):
        self.story()
        os.environ["OPWORK_BASE_URL"] = "https://work.example.test/"
        self.assertIn("https://work.example.test/projects/haynes-engineering/work_packages/10 ", self.run_cli("show", "10")[1])
        del os.environ["OPWORK_BASE_URL"]
        self.assertIn("https://work.haynesnetwork.com/projects/other/work_packages/10 ", self.run_cli("--project", "other", "show", "10")[1])

    def test_unsafe_base_is_refused_and_never_printed(self):
        self.story()
        for bad in ("http://work.haynesnetwork.com", "https://user:s3cr3t@work.haynesnetwork.com", "https://work.haynesnetwork.com/x", "https://work.haynesnetwork.com/?t=s3cr3t",
                    "work.haynesnetwork.com", "https://work.haynesnetwork.com:8443", "https://work.haynesnetwork.com/\n", "https://", "https://-bad.example"):
            os.environ["OPWORK_BASE_URL"] = bad
            code, out, err = self.run_cli("show", "10")
            self.assertEqual((code, out, err.strip()), (1, "", op.BASE_RULE), bad)

    def test_malformed_bases_that_make_urlsplit_raise_or_echo_never_leak(self):
        self.story()
        for bad in ("https://user\uff20s3cr3t.example", "https://[s3cr3t", "https://host:s3cr3t", "https://h\u00f6st.s3cr3t.example", "https://xn--s3cr3t-.example:99999"):
            os.environ["OPWORK_BASE_URL"] = bad
            code, out, err = self.run_cli("show", "10")
            self.assertEqual(code, 1, bad)
            self.assertNotIn("s3cr3t", out + err)
            self.assertEqual((out, err.strip()), ("", op.BASE_RULE), bad)

    def test_bad_base_stops_a_write_before_any_tracker_request(self):
        self.story()
        before = copy.deepcopy(self.fake.wps[10])
        os.environ["OPWORK_BASE_URL"] = "https://user:s3cr3t@work.haynesnetwork.com"
        for argv in (("claim", "10"), ("complete", "10", "--evidence", "e"), ("status", "10", "In review"), ("comment", "10", "hi"), CreateTests.args):
            code, out, err = self.run_cli(*argv)
            self.assertEqual((code, out, err.strip()), (1, "", op.BASE_RULE), argv)
        self.assertEqual(self.fake.calls, [])  # not even a read
        self.assertEqual(self.fake.wps[10], before)  # lock, owner and status untouched
        self.assertEqual(self.fake.acts[10], [])
        self.assertEqual(len(self.fake.wps), 3)  # nothing created
        self.assertEqual(self.run_cli("pending")[0], 0)  # offline commands do not print links, so they still work

    def test_no_github_links_are_generated(self):
        self.story()
        self.assertNotIn("github.com", self.run_cli("show", "10", "--history", "--relations")[1])
        self.assertNotIn("github.com", self.run_cli("search")[1])


class OutageTests(Base):
    def test_reads_fail_clearly_without_writing_anything(self):
        self.fake.fail["GET /api/v3"] = "before"
        code, _, err = self.run_cli("search")
        self.assertEqual(code, 5)
        self.assertIn("TRACKER UNAVAILABLE", err)
        self.assertNotIn("write", err)  # a failed read says nothing about writes
        self.assertFalse(os.path.exists(os.path.join(self.state.name, "pending")))

    def test_comment_during_outage_is_saved_then_flushed_exactly_once(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10"] = "before"
        code, _, err = self.run_cli("comment", "10", "checkpoint text")
        self.assertEqual(code, 5)
        self.assertIn("Saved locally", err)
        self.assertEqual(self.fake.acts[10], [])
        self.fake.fail.clear()
        out = self.run_cli("flush")[1]
        self.assertIn("flushed #10 (read back)", out)
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("]** checkpoint text\n\n(opwork:note=", self.fake.acts[10][0]["comment"]["raw"])
        self.assertEqual(self.run_cli("pending")[1].strip(), "no pending records")
        self.assertEqual(self.run_cli("flush")[1].strip(), "nothing pending")

    def test_comment_that_landed_before_the_error_is_not_duplicated_on_flush(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10"] = "after"
        self.assertEqual(self.run_cli("comment", "10", "maybe landed")[0], 5)
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.fake.fail.clear()
        self.assertIn("flushed", self.run_cli("flush")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)

    def test_explicit_checkpoint_needs_no_tracker(self):
        out = self.run_cli("checkpoint", "10", "branch X at abc123, next: run tests", up=False)
        self.assertEqual(out[0], 0)
        self.assertIn("NOT in OpenProject yet", out[1])
        self.assertIn("branch X at abc123", self.run_cli("pending", up=False)[1])

    def test_claim_during_outage_never_claims_success(self):
        self.story()
        self.fake.fail["PATCH /api/v3/work_packages"] = "before"
        code, out, err = self.run_cli("claim", "10")
        self.assertEqual(code, 5)
        self.assertIn("NOT APPLIED", err)
        self.assertNotIn("claimed", out)
        self.assertIsNone(self.fake.wps[10][op.OWNER])


    def pending_record(self):
        paths = list(Path(self.state.name, "pending").glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads(paths[0].read_text())

    def test_uncertain_comment_keeps_exact_body_and_key_then_reads_back_on_flush(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10"] = "after"
        code, out, err = self.run_cli("comment", "10", "exact checkpoint intent")
        self.assertEqual(code, 5)
        self.assertIn("UNCONFIRMED", err)
        self.assertNotIn("recorded", out)
        path, record = self.pending_record()
        original = self.fake.acts[10][0]["comment"]["raw"]
        self.assertEqual(record["body"], original)
        self.assertIn("opwork:note=" + record["token"], original)
        self.fake.fail.clear()
        self.fake.calls.clear()
        self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])
        self.assertFalse(path.exists())
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertEqual(self.posts(), [])
        self.assertTrue(any(m == "GET" and "/activities" in p for m, p in self.fake.calls))

    def test_comment_missing_on_readback_stays_pending_with_original_token(self):
        self.story()
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (201, {"id": 99}) if m == "POST" else original(m, u, p)
        code, out, err = self.run_cli("comment", "10", "invisible note")
        self.assertEqual(code, 6)
        self.assertIn("UNCONFIRMED", err)
        self.assertNotIn("recorded", out)
        path, record = self.pending_record()
        self.assertIn("opwork:note=" + record["token"], record["body"])
        self.assertEqual((record["post_status"], record["activity_id"]), (201, 99))
        self.assertIn("token " + record["token"], err)
        self.fake.handle = original
        self.fake.calls.clear()
        code, out, _ = self.run_cli("flush")  # the server accepted the POST: flush never reposts it
        self.assertEqual(code, 6)
        self.assertIn("needs manual review", out)
        self.assertTrue(path.exists())
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.fake.acts[10], [])
        # once the note shows up in history, flush confirms it and drops the record
        self.fake.acts[10].append({"id": 99, "createdAt": "2026-10-29T00:00:00Z", "comment": {"raw": record["body"]}, "details": [], "_links": {"user": {"href": "/api/v3/users/5"}}})
        self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])
        self.assertFalse(path.exists())
        self.assertEqual(self.posts(), [])

    def test_flush_retains_note_until_readback_is_available(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, before = self.pending_record()
        original = self.fake.handle
        def drop_after_post(method, url, payload):
            result = original(method, url, payload)
            if method == "POST":
                self.fake.fail["GET /api/v3/work_packages/10/activities"] = "before"
            return result
        self.fake.handle = drop_after_post
        self.assertEqual(self.run_cli("flush")[0], 5)
        after = json.loads(path.read_text())  # the accepted POST is remembered, nothing else changes
        self.assertRegex(after.pop("post_started"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")  # written before the POST
        self.assertEqual(after, dict(before, post_status=201, activity_id=1001))
        self.fake.fail.clear()
        self.fake.calls.clear()
        self.assertIn("read back", self.run_cli("flush")[1])
        self.assertFalse(path.exists())
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertEqual(self.posts(), [])

    def test_flush_does_not_post_when_history_is_incomplete(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (200, {"total": 101, "_embedded": {"elements": []}}) if m == "GET" and u.path.endswith("/activities") else original(m, u, p)
        code, out, err = self.run_cli("flush")
        self.assertIn("Incomplete history", out + err)
        self.pending_record()
        self.assertEqual(self.posts(), [])

    def test_transport_reset_after_post_is_pending_and_flush_recovers_without_duplicate(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10/activities"] = "after"
        def open_request(req, timeout):
            url = urllib.parse.urlsplit(req.full_url)
            try:
                code, data = self.fake.request(req.method, url.path + ("?" + url.query if url.query else ""),
                                               json.loads(req.data) if req.data else None)
            except Down:
                raise urllib.error.URLError(ConnectionResetError(errno.ECONNRESET, "reply lost"))
            response = io.BytesIO(json.dumps(data).encode())
            response.code = code
            return response
        self.tracker = op.Tracker(api.request, api.ApiUnavailable)
        # run_cli supplies the transport's actual unavailable type for this integration.
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = op.main(list(argv), self.tracker, api.ApiUnavailable)
            return code, out.getvalue(), err.getvalue()
        with mock.patch.object(api, "token", return_value="credential-sentinel"), \
             mock.patch.object(api.urllib.request, "build_opener", return_value=SimpleNamespace(open=open_request)):
            code, out, err = run("comment", "10", "transport checkpoint")
            self.assertEqual(code, 5)
            self.assertIn("UNCONFIRMED", err)
            self.assertNotIn("recorded", out)
            path, rec = self.pending_record()
            self.assertEqual(len(self.posts()), 1)
            self.assertIn("opwork:note=" + rec["token"], rec["body"])
            for stored in Path(self.state.name).rglob("*"):
                if stored.is_file():
                    self.assertNotIn("credential-sentinel", stored.read_text())
            self.fake.fail.clear()
            self.assertIn("flushed #10 (read back)", run("flush")[1])
            self.assertFalse(path.exists())
            self.assertEqual(len(self.posts()), 1)
            self.assertEqual(len(self.fake.acts[10]), 1)

    def test_uncertain_patch_readback_mismatch_stays_pending(self):
        self.story()
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (200, {}) if m == "PATCH" else original(m, u, p)
        argv = ["claim", "10", "--note", "keep this intent"]
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 6)
        self.assertIn("UNCONFIRMED", err)
        self.assertNotIn("claimed", out)
        _, rec = self.pending_record()
        self.assertEqual(rec["argv"], argv)

    def test_lifecycle_note_failure_keeps_existing_note_key_and_body(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10/activities"] = "after"
        argv = ["claim", "10", "--note", "checkpoint during claim"]
        self.assertEqual(self.run_cli(*argv)[0], 5)
        _, rec = self.pending_record()
        body = self.fake.acts[10][0]["comment"]["raw"]
        self.assertIn("opwork:note=" + rec["token"], body)
        self.assertEqual(rec["body"], body)
        self.assertEqual(rec["argv"], argv)

    def test_uncertain_patch_preserves_original_command_intent(self):
        self.story()
        self.fake.fail["PATCH /api/v3/work_packages"] = "after"
        self.fake.fail["GET /api/v3/work_packages/10"] = "before"
        # Permit the initial snapshot, then fail the read after PATCH.
        original = self.fake.request
        first = True
        def request(method, path, payload=None):
            nonlocal first
            if method == "GET" and first:
                first = False
                return 200, copy.deepcopy(self.fake.wps[10])
            return original(method, path, payload)
        self.tracker.request = request
        argv = ["claim", "10", "--note", "exact intent"]
        self.assertEqual(self.run_cli(*argv)[0], 5)
        _, rec = self.pending_record()
        self.assertEqual(rec["argv"], argv)

    def accept_but_hide_posts(self):
        """The server answers 201 to a POST but the note never shows on read back."""
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (201, {"id": 99}) if m == "POST" and u.path.endswith("/activities") else original(m, u, p)
        return original

    def test_2xx_unseen_note_is_never_reposted_across_two_flushes(self):
        self.story()
        self.accept_but_hide_posts()
        self.assertEqual(self.run_cli("comment", "10", "invisible note")[0], 6)
        path, record = self.pending_record()
        self.assertEqual((record["post_status"], record["activity_id"]), (201, 99))
        for _ in range(2):
            code, out, _ = self.run_cli("flush")
            self.assertEqual(code, 6)
            self.assertIn("needs manual review", out)
            self.assertIn(record["token"], out)
            self.assertEqual(json.loads(path.read_text()), record)
        self.assertEqual(len(self.posts("/activities")), 1)  # only the original comment

    def test_flush_that_gets_a_2xx_unseen_records_it_and_never_reposts_again(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        self.accept_but_hide_posts()
        path, before = self.pending_record()
        code, out, _ = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertIn("kept", out)
        after = json.loads(path.read_text())
        self.assertTrue(after.pop("post_started"))
        self.assertEqual(after, dict(before, post_status=201, activity_id=99))
        self.run_cli("flush")
        self.run_cli("flush")
        self.assertEqual(len(self.posts("/activities")), 1)

    def started_note(self, **extra):
        """A pending checkpoint of #10 whose flush POST was started (marker on disk) and then lost."""
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, record = self.pending_record()
        path.write_text(json.dumps(dict(record, post_started="2026-10-31T05:00:00Z", **extra)))
        return path, record

    def forbid_writes(self, marker):
        """Make write_record raise OSError for any record that carries `marker`; others pass through."""
        real = op.write_record
        def write(path, record):
            if marker in record:
                raise OSError(28, "No space left on device")
            return real(path, record)
        return mock.patch.object(op, "write_record", side_effect=write)

    def test_started_marker_with_the_token_absent_goes_to_manual_review_never_reposted(self):
        path, record = self.started_note()
        for _ in range(2):
            code, out, _ = self.run_cli("flush")
            self.assertEqual(code, 6)
            self.assertIn("needs manual review", out)
            self.assertIn("2026-10-31T05:00:00Z", out)
            self.assertIn(record["token"], out)
            self.assertTrue(path.exists())
        self.assertEqual(self.posts(), [])
        self.assertIn("POST started 2026-10-31T05:00:00Z", self.run_cli("pending")[1])

    def test_started_marker_with_the_token_in_history_is_confirmed_and_removed(self):
        path, record = self.started_note()
        self.fake.acts[10] = [{"id": 1, "comment": {"raw": "x (opwork:note=%s)" % record["token"]}, "createdAt": "2026-10-31T05:00:01Z"}]
        self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])
        self.assertFalse(path.exists())
        self.assertEqual(self.posts(), [])

    def test_flush_writes_the_started_marker_before_the_post(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, _ = self.pending_record()
        seen = []
        original = self.fake.handle
        def spy(method, url, payload):
            if method == "POST":
                seen.append(json.loads(path.read_text()).get("post_started"))
            return original(method, url, payload)
        self.fake.handle = spy
        self.assertIn("flushed", self.run_cli("flush")[1])
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0])

    def test_marker_write_failure_means_no_post_and_exit_6_naming_the_token(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        self.run_cli("checkpoint", "10", "second note")
        path = sorted(Path(self.state.name, "pending").glob("*.json"))[0]
        record = json.loads(path.read_text())
        with self.forbid_writes("post_started"):
            code, out, err = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertEqual(self.posts(), [])
        self.assertIn("UNCONFIRMED", err)
        self.assertIn(record["token"], err)
        self.assertNotIn("Traceback", err)
        self.assertIn("1 later record(s) not attempted", out)
        self.assertEqual(json.loads(path.read_text()), record)  # untouched

    def reject_posts(self, code):
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (code, {"message": "no"}) if m == "POST" and u.path.endswith("/activities") else original(m, u, p)
        return original

    def test_a_clean_4xx_during_flush_clears_the_marker_and_is_posted_on_the_next_flush(self):
        for code in (403, 422):
            with self.subTest(code=code):
                self.setUp()
                self.story()
                self.run_cli("checkpoint", "10", "offline evidence")
                path, before = self.pending_record()
                original = self.reject_posts(code)
                status, out, err = self.run_cli("flush")
                self.assertEqual(self.posts("/activities")[0][0], "POST")
                self.assertIn("rejected HTTP %d" % code, out)
                self.assertEqual(json.loads(path.read_text()), before)  # no post_started: the marker was cleared
                self.fake.handle = original
                self.fake.calls.clear()
                status, out, _ = self.run_cli("flush")
                self.assertEqual(status, 0)
                self.assertIn("flushed #10 (read back)", out)
                self.assertEqual(len(self.posts("/activities")), 1)
                self.assertFalse(path.exists())

    def test_a_3xx_during_flush_keeps_the_marker_for_manual_review(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, before = self.pending_record()
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (302, {}) if m == "POST" and u.path.endswith("/activities") else original(m, u, p)
        code, out, err = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertTrue(json.loads(path.read_text()).get("post_started"))
        self.fake.handle = original
        self.fake.calls.clear()
        code, out, _ = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertIn("needs manual review", out)
        self.assertEqual(self.posts(), [])

    def test_a_4xx_whose_marker_cannot_be_cleared_keeps_it_and_says_so(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, before = self.pending_record()
        self.reject_posts(422)
        real, calls = op.write_record, []
        def write(target, record):
            calls.append(record)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return real(target, record)
        with mock.patch.object(op, "write_record", side_effect=write):
            code, out, err = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertIn(before["token"], err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(json.loads(path.read_text()).get("post_started"))

    def test_write_record_fsyncs_the_file_and_then_the_directory(self):
        target = os.path.join(self.state.name, "pending-fsync.json")
        with mock.patch.object(op.os, "fsync", wraps=os.fsync) as fsync:
            op.write_record(target, {"a": 1})
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(json.loads(Path(target).read_text()), {"a": 1})

    def test_a_failing_local_save_does_not_hide_the_original_uncertain_text(self):
        self.story()
        self.fake.handle_real = self.fake.handle
        self.fake.handle = lambda m, u, p: (200, {}) if m == "PATCH" else self.fake.handle_real(m, u, p)
        with mock.patch.object(op, "write_record", side_effect=OSError(30, "Read-only file system")):
            code, _, err = self.run_cli("claim", "10")
        self.assertEqual(code, 6)
        self.assertIn("UNCERTAIN", err)
        self.assertLess(err.index("UNCERTAIN"), err.index("could not be saved locally"))

    def test_flush_exits_6_for_a_kept_intent_and_0_when_nothing_is_kept(self):
        self.assertEqual(self.run_cli("flush")[0], 0)
        self.story()
        original = self.fake.handle
        self.fake.handle = lambda m, u, p: (200, {}) if m == "PATCH" else original(m, u, p)
        self.assertEqual(self.run_cli("claim", "10")[0], 6)
        code, out, err = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertIn("kept", out)
        self.assertIn("manual review", err)

    def test_record_write_failure_after_a_2xx_posts_exactly_once_across_two_flushes(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        self.accept_but_hide_posts()
        path, record = self.pending_record()
        with self.forbid_writes("post_status"):
            code, out, err = self.run_cli("flush")
        self.assertEqual(code, 6)
        self.assertIn("UNCONFIRMED", err)
        self.assertIn(record["token"], err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(len(self.posts("/activities")), 1)
        code, out, _ = self.run_cli("flush")  # the marker written before the POST survived
        self.assertEqual(code, 6)
        self.assertIn("needs manual review", out)
        self.assertEqual(len(self.posts("/activities")), 1)

    def test_record_write_failure_while_saving_an_unavailable_comment_shows_the_original_failure_and_exit_5(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10/activities"] = "after"
        with mock.patch.object(op, "write_record", side_effect=OSError(30, "Read-only file system")):
            code, out, err = self.run_cli("comment", "10", "lands then the disk is read-only")
        self.assertEqual(code, 5)  # the original failure's code, not the local one
        self.assertIn("TRACKER UNAVAILABLE", err)
        self.assertLess(err.index("TRACKER UNAVAILABLE"), err.index("could not be saved locally"))
        self.assertIn("UNCONFIRMED", err)
        self.assertRegex(err, r"note token [0-9a-f]{12}")
        self.assertNotIn("Traceback", err)

    def test_note_record_with_a_null_body_is_valid_and_flushes(self):
        self.story()
        self.run_cli("checkpoint", "10", "offline evidence")
        path, record = self.pending_record()
        path.write_text(json.dumps(dict(record, body=None)))
        self.assertNotIn("corrupt", self.run_cli("pending")[1])
        self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])
        self.assertEqual(len(self.fake.acts[10]), 1)
        self.assertIn("Deferred checkpoint", self.fake.acts[10][0]["comment"]["raw"])

    def test_pending_says_read_back_unconfirmed_not_not_visible(self):
        self.story()
        self.accept_but_hide_posts()
        self.run_cli("comment", "10", "invisible note")
        listing = self.run_cli("pending")[1]
        self.assertIn("POST accepted HTTP 201, read back unconfirmed", listing)
        self.assertNotIn("not visible on read back", listing)

    def test_readback_failures_after_a_2xx_are_saved_unconfirmed(self):
        original = self.fake.handle
        cases = {
            "a 4xx on the read back": lambda m, u, p: (404, {"message": "gone"}) if m == "GET" and u.path.endswith("/activities") else original(m, u, p),
            "a malformed read back": lambda m, u, p: (200, {}) if m == "GET" and u.path.endswith("/activities") else original(m, u, p),
            "a non-list read back": lambda m, u, p: (200, {"_embedded": {"elements": 5}}) if m == "GET" and u.path.endswith("/activities") else original(m, u, p),
        }
        for name, handler in cases.items():
            with self.subTest(name):
                self.story()
                for stale in Path(self.state.name, "pending").glob("*.json") if Path(self.state.name, "pending").exists() else []:
                    stale.unlink()
                self.fake.acts[10] = []
                self.fake.handle = handler
                code, out, err = self.run_cli("comment", "10", "lands but cannot be read")
                self.assertEqual(code, 6)
                self.assertIn("UNCONFIRMED", err)
                self.assertNotIn("recorded", out)
                path, record = self.pending_record()
                self.assertEqual(record["kind"], "note")
                self.assertEqual(record["post_status"], 201)
                self.assertIn("opwork:note=" + record["token"], record["body"])
                self.assertEqual(len(self.fake.acts[10]), 1)
                self.fake.handle = original
                self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])  # present in history: confirmed, not reposted
                self.assertEqual(len(self.fake.acts[10]), 1)
        self.fake.handle = original

    def test_readback_unavailable_after_a_2xx_records_the_accepted_post(self):
        self.story()
        original = self.fake.handle
        def drop_after_post(method, url, payload):
            result = original(method, url, payload)
            if method == "POST":
                self.fake.fail["GET /api/v3/work_packages/10/activities"] = "before"
            return result
        self.fake.handle = drop_after_post
        self.assertEqual(self.run_cli("comment", "10", "posted then the tracker dropped")[0], 5)
        _, record = self.pending_record()
        self.assertEqual((record["post_status"], record["activity_id"]), (201, 1001))
        self.fake.fail.clear()
        self.fake.calls.clear()
        self.assertIn("flushed #10 (read back)", self.run_cli("flush")[1])
        self.assertEqual(self.posts(), [])

    def test_flush_reports_unconfirmed_per_record_and_prints_partial_output(self):
        for i in (10, 11, 12):
            self.story(i)
        clock = itertools.count(1000)
        with mock.patch.object(op.time, "time", side_effect=lambda: float(next(clock))):
            for i in (10, 11, 12):
                self.run_cli("checkpoint", str(i), "note for %d" % i)
        self.fake.fail["POST /api/v3/work_packages/11/activities"] = "before"
        code, out, err = self.run_cli("flush")
        self.assertEqual(code, 5)
        self.assertIn("flushed #10 (read back)", out)  # the partial output is kept
        self.assertIn("UNCONFIRMED", out)
        self.assertIn("1 later record(s) not attempted", out)
        self.assertNotIn("The read failed", err)
        self.assertEqual(len(list(Path(self.state.name, "pending").glob("*.json"))), 2)
        self.fake.fail.clear()
        self.fake.calls.clear()
        code, out, _ = self.run_cli("flush")
        self.assertEqual(code, 6)
        # the POST for #11 was started and its outcome is unknown: kept for review, never reposted
        self.assertIn("needs manual review", out)
        self.assertEqual(out.count("flushed"), 1)
        self.assertEqual([len(self.fake.acts[i]) for i in (10, 11, 12)], [1, 0, 1])
        self.assertEqual([c[1] for c in self.posts("/activities")], ["/api/v3/work_packages/12/activities"])

    def test_kept_intent_message_and_pending_show_the_note_token(self):
        self.story()
        self.fake.fail["POST /api/v3/work_packages/10/activities"] = "after"
        argv = ["claim", "10", "--note", "claim intent"]
        self.run_cli(*argv)
        _, record = self.pending_record()
        pending = self.run_cli("pending")[1]
        self.assertIn("token=" + record["token"], pending)
        out = self.run_cli("flush")[1]
        self.assertIn("kept", out)
        self.assertIn("manual inspection (show/search before repeating; note token %s may have landed)" % record["token"], out)
        self.assertNotIn("re-run it yourself", out)

    def test_pending_store_is_private_and_written_atomically(self):
        old = os.umask(0)
        self.addCleanup(os.umask, old)
        os.environ["OPWORK_STATE_DIR"] = os.path.join(self.state.name, "fresh", "opwork")
        self.run_cli("checkpoint", "10", "private note")
        pending = Path(os.environ["OPWORK_STATE_DIR"], "pending")
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode) & 0o777, 0o700)
        self.assertEqual(stat.S_IMODE(pending.parent.stat().st_mode) & 0o777, 0o700)
        (path,) = pending.glob("*.json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o777, 0o600)
        before = path.read_text()
        with mock.patch.object(op.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                op.write_record(str(path), {"kind": "note", "replaced": True})
        self.assertEqual(path.read_text(), before)  # the old record survives a failed write
        self.assertEqual([x.name for x in pending.iterdir()], [path.name])  # and no temp file is left behind

    def test_corrupt_records_are_reported_and_skipped_not_fatal(self):
        self.story()
        self.run_cli("checkpoint", "10", "good note")
        pending = Path(self.state.name, "pending")
        (pending / "1-garbage.json").write_text("{not json")
        (pending / "2-list.json").write_text("[]")
        (pending / "3-partial.json").write_text(json.dumps({"kind": "note", "token": "abc"}))
        code, out, _ = self.run_cli("pending")
        self.assertEqual(code, 0)
        self.assertIn("good note", out)
        self.assertEqual(out.count("corrupt record"), 3)
        code, out, _ = self.run_cli("flush")
        self.assertEqual(code, 0)
        self.assertIn("flushed #10 (read back)", out)
        self.assertEqual(out.count("skipped corrupt record"), 3)
        self.assertEqual(sorted(x.name for x in pending.iterdir()), ["1-garbage.json", "2-list.json", "3-partial.json"])


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.elapsed, self.sleeps, self.calls = 0, [], []
        self.stderr = io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stack.enter_context(mock.patch.object(api, "token", return_value="credential-sentinel"))
        self.stack.enter_context(mock.patch.object(api, "time", SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep), create=True))
        self.opener = SimpleNamespace(open=self.open)
        self.stack.enter_context(mock.patch.object(api.urllib.request, "build_opener", return_value=self.opener))
        self.outcomes = []

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.elapsed += delay

    def response(self, code=200, raw=b'{"id": 12}'):
        response = io.BytesIO(raw)
        response.code = code
        return response

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        result = self.outcomes.pop(0)
        if callable(result):
            return result(timeout)
        if isinstance(result, Exception):
            raise result
        return result

    def test_transient_connection_failures_then_success_same_request(self):
        failures = [OSError(n, "secret-error-sentinel") for n in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT, errno.ECONNRESET)]
        failures += [TimeoutError("secret-error-sentinel")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, errno=failure.errno):
                self.calls.clear()
                self.outcomes = [urllib.error.URLError(failure), self.response()]
                self.assertEqual(api.request("GET", "/api/v3/work_packages/12?secret-query-sentinel"), (200, {"id": 12}))
                self.assertEqual(len(self.calls), 2)
                self.assertIs(self.calls[0][0], self.calls[1][0])
                self.assertLessEqual(self.calls[-1][1], 30)

    def test_transient_http_then_success_closes_failure(self):
        for code in (502, 503, 504):
            with self.subTest(code=code):
                failed = urllib.error.HTTPError("secret-url-sentinel", code, "secret-reason-sentinel", {}, io.BytesIO(b"secret-body-sentinel"))
                self.outcomes = [failed, self.response()]
                self.assertEqual(api.request("GET", "/api/v3/users/me")[0], 200)
                self.assertTrue(failed.closed)
                self.assertNotIn("sentinel", self.stderr.getvalue())

    def test_budget_exhausted_is_finite_with_capped_backoff(self):
        self.outcomes = [urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "down"))] * 200
        with self.assertRaisesRegex(api.ApiUnavailable, "GET.*120.*budget exhausted"):
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(self.elapsed, api.GET_BUDGET - api.RETRY_FLOOR)  # the last attempt starts with the floor left
        self.assertLess(len(self.calls), 20)
        self.assertLessEqual(max(self.sleeps), 16)
        self.assertEqual(self.sleeps[:4], [1, 2, 4, 8])

    def test_attempt_time_counts_toward_budget(self):
        def timeout(seconds):
            self.elapsed += seconds
            raise TimeoutError()
        self.outcomes = [timeout] * 20
        with self.assertRaises(api.ApiUnavailable):
            api.request("GET", "/api/v3/users/me")
        self.assertGreaterEqual(self.elapsed, api.GET_BUDGET - api.RETRY_FLOOR)
        self.assertLessEqual(self.elapsed, api.GET_BUDGET)
        self.assertLessEqual(len(self.calls), 4)
        self.assertLess(self.calls[-1][1], 30)

    def test_denials_and_other_nontransient_http_are_tried_once(self):
        for code in (401, 403, 400, 404, 429, 500, 501, 302):
            with self.subTest(code=code):
                self.calls.clear()
                self.outcomes = [self.response(code)]
                if code >= 500:
                    with self.assertRaises(api.ApiUnavailable):
                        api.request("GET", "/api/v3/users/me")
                else:
                    self.assertEqual(api.request("GET", "/api/v3/users/me")[0], code)
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(self.sleeps)

    def test_denial_body_read_failure_does_not_retry(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                self.calls.clear()
                response = self.response(code)
                response.read = mock.Mock(side_effect=TimeoutError())
                self.outcomes = [response, self.response()]
                with self.assertRaises(api.ApiUnavailable):
                    api.request("GET", "/api/v3/users/me")
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(self.sleeps)

    def test_recovery_covers_observed_67_second_gap(self):
        def during_gap(timeout):
            if self.elapsed < 67:
                raise OSError(errno.EHOSTUNREACH, "down")
            return self.response()
        self.outcomes = [during_gap] * 20
        self.assertEqual(api.request("GET", "/api/v3/users/me")[0], 200)
        self.assertGreaterEqual(self.elapsed, 67)
        self.assertLess(self.elapsed, 120)

    def test_mutations_are_never_retried(self):
        for method in ("POST", "PATCH"):
            for failure in (ConnectionResetError(errno.ECONNRESET, "down"), self.response(503)):
                with self.subTest(method=method, failure=type(failure).__name__):
                    self.calls.clear()
                    self.outcomes = [failure]
                    with self.assertRaises(api.ApiUnavailable):
                        api.request(method, "/api/v3/work_packages/12", {"subject": "intent"})
                    self.assertEqual(len(self.calls), 1)
                    self.assertFalse(self.sleeps)

    def test_nontransient_transport_error_is_not_retried(self):
        self.outcomes = [urllib.error.URLError(OSError(errno.EACCES, "secret-error-sentinel"))]
        with self.assertRaises(api.ApiUnavailable):
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.sleeps)

    def test_read_failure_retries_only_get(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                self.calls.clear()
                response = self.response()
                response.read = mock.Mock(side_effect=ConnectionResetError(errno.ECONNRESET, "down"))
                self.outcomes = [response, self.response()]
                if method == "GET":
                    self.assertEqual(api.request(method, "/api/v3/users/me")[0], 200)
                    self.assertEqual(len(self.calls), 2)
                else:
                    with self.assertRaises(api.ApiUnavailable):
                        api.request(method, "/api/v3/work_packages/12", {})
                    self.assertEqual(len(self.calls), 1)
                self.assertTrue(response.closed)

    def test_mutations_raising_a_5xx_httperror_are_never_retried(self):
        for method in ("POST", "PATCH"):
            for code in (500, 502, 503, 504):
                with self.subTest(method=method, code=code):
                    self.calls.clear()
                    self.outcomes = [urllib.error.HTTPError("secret-url-sentinel", code, "secret-reason", {}, io.BytesIO(b"secret-body-sentinel"))]
                    with self.assertRaises(api.ApiUnavailable):
                        api.request(method, "/api/v3/work_packages/12", {"subject": "intent"})
                    self.assertEqual(len(self.calls), 1)
                    self.assertFalse(self.sleeps)

    def test_temporary_dns_failure_and_incomplete_read_retry_for_get_only(self):
        dns = lambda: urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "secret-error-sentinel"))
        def incomplete():
            response = self.response()
            response.read = mock.Mock(side_effect=http.client.IncompleteRead(b"par", 10))
            return response
        for name, failure in (("EAI_AGAIN", dns), ("IncompleteRead", incomplete)):
            with self.subTest(name, method="GET"):
                self.calls.clear()
                self.outcomes = [failure(), self.response()]
                self.assertEqual(api.request("GET", "/api/v3/users/me")[0], 200)
                self.assertEqual(len(self.calls), 2)
            with self.subTest(name, method="POST"):
                self.calls.clear()
                self.sleeps.clear()
                self.outcomes = [failure(), self.response()]
                with self.assertRaises(api.ApiUnavailable):
                    api.request("POST", "/api/v3/work_packages/12/activities", {})
                self.assertEqual(len(self.calls), 1)
                self.assertFalse(self.sleeps)
        self.assertNotIn("sentinel", self.stderr.getvalue())

    def test_permanent_dns_failure_is_not_retried(self):
        self.outcomes = [urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "secret-error-sentinel"))]
        with self.assertRaises(api.ApiUnavailable):
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.calls), 1)

    def test_no_attempt_starts_with_less_than_the_floor_left(self):
        def nearly_all(timeout):
            self.elapsed += api.GET_BUDGET - api.RETRY_FLOOR / 2
            raise OSError(errno.ECONNREFUSED, "down")
        self.outcomes = [nearly_all, self.response()]
        with self.assertRaisesRegex(api.ApiUnavailable, "budget exhausted"):
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.sleeps)
        self.assertLessEqual(self.elapsed, api.GET_BUDGET)

    def test_budget_message_is_formatted_from_the_budget(self):
        self.outcomes = [urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "down"))] * 50
        with mock.patch.object(api, "GET_BUDGET", 30):
            with self.assertRaises(api.ApiUnavailable) as raised:
                api.request("GET", "/api/v3/users/me")
        self.assertIn("30 s retry budget exhausted", str(raised.exception))
        self.assertNotIn("120", str(raised.exception))
        self.assertLessEqual(self.elapsed, 30)

    def test_no_diagnostic_for_an_expected_4xx_answer(self):
        for method in ("GET", "POST", "PATCH"):
            for code in (404, 409, 422):
                with self.subTest(method=method, code=code):
                    self.outcomes = [self.response(code, b'{"message": "no"}')]
                    self.assertEqual(api.request(method, "/api/v3/work_packages/12", None if method == "GET" else {})[0], code)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_diagnostic_lines_are_only_for_attempts_that_retry_or_end_unavailable(self):
        self.outcomes = [self.response(503), self.response(502), self.response(200)]
        api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 2)  # the two retried failures, not the success
        self.stderr.truncate(0)
        self.stderr.seek(0)
        self.outcomes = [self.response(500)]
        with self.assertRaises(api.ApiUnavailable):
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)

    def test_diagnostic_is_bounded_and_has_no_credentials_or_request_data(self):
        self.outcomes = [urllib.error.URLError(OSError(errno.EACCES, "credential-sentinel\nsecret-error-sentinel"))]
        with self.assertRaises(api.ApiUnavailable):
            api.request("GET", "/api/v3/users/me?secret-query-sentinel")
        lines = self.stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ GET host=openproject-web.frontend.svc.cluster.local category=[a-z_]+ errno=13$")
        self.assertLess(len(lines[0]), 200)
        self.assertNotIn("sentinel", lines[0])


TOKEN = "opapi-" + "ab12" * 16  # 70 characters, the dedicated token's shape
HELPER_ARGV = ["kubectl", "exec", "-n", "dev", "deploy/openproject-agent-credentials", "-c", "credentials", "--", "cat", "/run/openproject-agent/token"]


class HelperTests(unittest.TestCase):
    """The token-only helper phase, with a fake subprocess.run (no kubectl) and a fake clock."""

    def setUp(self):
        self.elapsed, self.sleeps, self.runs, self.http = 0, [], [], []
        self.stderr = io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stack.enter_context(mock.patch.object(api, "_token", None))
        self.stack.enter_context(mock.patch.object(api, "time", SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep), create=True))
        self.stack.enter_context(mock.patch.object(api.subprocess, "run", side_effect=self.fake_run))
        self.stack.enter_context(mock.patch.object(api.urllib.request, "build_opener", return_value=SimpleNamespace(open=self.open)))
        self.outcomes = []

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.elapsed += delay

    def fake_run(self, argv, **kwargs):
        self.runs.append((argv, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    default = SimpleNamespace(returncode=1, stdout=b"", stderr=b"error: unable to upgrade connection: container not found (stderr-sentinel)")

    def open(self, req, timeout):
        self.http.append((req.get_method(), req.get_header("Authorization"), timeout))
        response = io.BytesIO(b'{"id": 12}')
        response.code = 200
        return response

    @staticmethod
    def helper(returncode=0, stdout=TOKEN.encode(), stderr=b""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def assert_nothing_leaked(self, *texts):
        for text in (self.stderr.getvalue(),) + texts:
            for sentinel in ("sentinel", "ab12ab12", "Forbidden", "upgrade connection"):
                self.assertNotIn(sentinel, text)

    def test_get_retries_a_transient_helper_failure_then_succeeds_and_caches(self):
        self.outcomes = [self.default, self.helper()]
        self.assertEqual(api.request("GET", "/api/v3/users/me"), (200, {"id": 12}))
        self.assertEqual([argv for argv, _ in self.runs], [HELPER_ARGV] * 2)  # the same helper, twice
        self.assertEqual(self.runs[0][1]["env"]["KUBECTL_REMOTE_COMMAND_WEBSOCKETS"], "false")
        self.assertEqual(self.sleeps, [1])
        self.assertEqual(self.http, [("GET", "Bearer " + TOKEN, mock.ANY)])
        lines = self.stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ helper category=transient attempt=1$")
        self.assert_nothing_leaked()
        api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.runs), 2)  # cached for the process: no third helper read

    def test_a_helper_timeout_is_retried_and_labelled_timeout(self):
        self.outcomes = [subprocess_timeout(), OSError(11, "stderr-sentinel"), self.helper()]
        self.assertEqual(api.request("GET", "/api/v3/users/me")[0], 200)
        categories = re.findall(r"category=(\w+) attempt=(\d)", self.stderr.getvalue())
        self.assertEqual(categories, [("timeout", "1"), ("transient", "2")])
        self.assert_nothing_leaked()

    def test_get_exhaustion_is_finite_within_the_get_budget_and_ends_in_api_unavailable(self):
        with self.assertRaises(api.ApiUnavailable) as caught:
            api.request("GET", "/api/v3/users/me")
        self.assertEqual(self.elapsed, api.GET_BUDGET - api.RETRY_FLOOR)
        self.assertGreater(len(self.runs), 2)
        self.assertLess(len(self.runs), 20)
        self.assertLessEqual(max(self.sleeps), api.BACKOFF_CAP)
        self.assertTrue(all(1 <= kwargs["timeout"] <= api.HELPER_TIMEOUT for _, kwargs in self.runs))
        self.assertEqual(self.http, [])  # the HTTP request was never built or sent
        self.assertIsNone(api._token)
        self.assertRegex(str(caught.exception), r"transient after %d attempt\(s\) \(retry budget exhausted\)" % len(self.runs))
        self.assert_nothing_leaked(str(caught.exception))
        self.assertEqual(len(self.stderr.getvalue().splitlines()), len(self.runs))

    def test_helper_time_counts_toward_the_get_budget(self):
        def slow(argv, **kwargs):
            self.elapsed += min(100, kwargs["timeout"])  # a slow helper, killed at its timeout
            return self.default
        with mock.patch.object(api.subprocess, "run", side_effect=slow):
            with self.assertRaises(api.ApiUnavailable):
                api.request("GET", "/api/v3/users/me")
        self.assertLessEqual(self.elapsed, api.GET_BUDGET)

    def test_an_authorization_denial_is_tried_once(self):
        for text in (b'Error from server (Forbidden): pods "x" is forbidden: User cannot exec', b"error: You must be logged in: UNAUTHORIZED stderr-sentinel"):
            with self.subTest(text=text[:20]):
                self.runs.clear()
                self.sleeps.clear()
                self.outcomes = [self.helper(1, b"", text)]
                with self.assertRaisesRegex(api.ApiUnavailable, "denied after 1 attempt") as caught:
                    api.request("GET", "/api/v3/users/me")
                self.assertEqual((len(self.runs), self.sleeps, self.http), (1, [], []))
                self.assert_nothing_leaked(str(caught.exception))
        self.assertIn("category=denied attempt=1", self.stderr.getvalue())

    def test_an_invalid_token_is_tried_once_and_never_echoed(self):
        for stdout in (b"opapi-short stdout-sentinel", b"opapi-" + b"zz" * 32 + b"sentinel", b"", b"\xff\xfe sentinel"):
            with self.subTest(stdout=stdout[:12]):
                self.runs.clear()
                self.outcomes = [self.helper(0, stdout)]
                with self.assertRaisesRegex(api.ApiUnavailable, "invalid after 1 attempt") as caught:
                    api.request("GET", "/api/v3/users/me")
                self.assertEqual((len(self.runs), self.sleeps, self.http), (1, [], []))
                self.assert_nothing_leaked(str(caught.exception))
                self.assertNotIn("opapi-", str(caught.exception) + self.stderr.getvalue())
        self.assertIsNone(api._token)

    def test_a_token_on_stdout_of_a_failed_helper_is_not_used(self):
        self.outcomes = [self.helper(1, TOKEN.encode(), b"container not found"), self.helper()]
        api.request("GET", "/api/v3/users/me")
        self.assertEqual(len(self.runs), 2)
        self.assertNotIn(TOKEN, self.stderr.getvalue())

    def test_a_mutation_retries_only_the_helper_read_and_sends_exactly_one_post(self):
        for method in ("POST", "PATCH"):
            with self.subTest(method=method):
                api._token = None
                self.runs.clear()
                self.http.clear()
                self.outcomes = [self.default, self.default, self.helper()]
                self.assertEqual(api.request(method, "/api/v3/work_packages/12/activities", {"comment": {"raw": "x"}})[0], 200)
                self.assertEqual(len(self.runs), 3)
                self.assertEqual(self.http, [(method, "Bearer " + TOKEN, 30)])  # one send, the fixed mutation timeout

    def test_a_mutation_whose_helper_never_recovers_sends_nothing_within_its_small_budget(self):
        with self.assertRaises(api.ApiUnavailable):
            api.request("POST", "/api/v3/work_packages/12/activities", {"comment": {"raw": "x"}})
        self.assertEqual(self.http, [])
        self.assertEqual(self.elapsed, api.HELPER_BUDGET - api.RETRY_FLOOR)
        self.assertLess(api.HELPER_BUDGET, api.GET_BUDGET)
        self.assertGreater(len(self.runs), 2)
        self.assertLess(len(self.runs), 20)

    def test_a_mutation_denial_and_invalid_token_are_tried_once(self):
        for outcome in (self.helper(1, b"", b"forbidden"), self.helper(0, b"not-a-token")):
            self.runs.clear()
            self.outcomes = [outcome]
            with self.assertRaises(api.ApiUnavailable):
                api.request("PATCH", "/api/v3/work_packages/12", {"lockVersion": 1})
            self.assertEqual((len(self.runs), self.http), (1, []))

    def opwork_comment(self, state):
        env = {"OPWORK_STATE_DIR": state, "OPWORK_SESSION": "codex-test | worktree w1 | branch agent/w1"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(op, "load_api", return_value=api), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = op.main(["comment", "327", "work done"])
        return code, out.getvalue(), err.getvalue()

    def test_opwork_keeps_a_comment_pending_when_the_helper_never_recovers(self):
        with tempfile.TemporaryDirectory() as state:
            code, out, err = self.opwork_comment(state)
            self.assertEqual(code, 5)
            self.assertIn("UNCONFIRMED", err)
            self.assertEqual(self.http, [])  # zero HTTP POSTs
            files = list(Path(state, "pending").glob("*.json"))
            self.assertEqual(len(files), 1)
            record = json.loads(files[0].read_text())
            self.assertEqual((record["kind"], record["wp"], record["text"]), ("note", 327, "work done"))
            self.assertNotIn("post_status", record)
            self.assert_nothing_leaked(out, err, files[0].read_text())

    def test_opwork_posts_once_when_the_helper_recovers_during_the_comment(self):
        self.outcomes = [self.default, self.helper()]
        with tempfile.TemporaryDirectory() as state:
            self.opwork_comment(state)
            self.assertEqual([method for method, _, _ in self.http].count("POST"), 1)


def subprocess_timeout():
    return api.subprocess.TimeoutExpired(HELPER_ARGV, 30, output=b"stdout-sentinel", stderr=b"stderr-sentinel")


class RealOpenerTests(unittest.TestCase):
    """The real urllib opener and NoRedirect against a local http.server on 127.0.0.1 (no OpenProject)."""

    def setUp(self):
        outer = self
        self.log, self.attempts = [], {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.log.append((self.command, self.path, self.headers.get("Authorization")))
                count = outer.attempts[self.path] = outer.attempts.get(self.path, 0) + 1
                if self.path == "/api/v3/work_packages/1":  # a redirect that must not be followed
                    self.send_response(302)
                    self.send_header("Location", "/api/v3/types")
                    self.end_headers()
                elif self.path == "/api/v3/work_packages/2" and count == 1 or self.path.endswith("/activities"):
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    body = b'{"id": 2}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            do_GET = do_POST = do_PATCH = handle_request

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(api, "ORIGIN", "http://127.0.0.1:%d" % self.server.server_address[1]))
        stack.enter_context(mock.patch.object(api, "token", return_value="credential-sentinel"))
        stack.enter_context(mock.patch.object(api, "time", SimpleNamespace(monotonic=time.monotonic, sleep=lambda seconds: None), create=True))
        stack.enter_context(mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"}))
        stderr = io.StringIO()
        stack.enter_context(contextlib.redirect_stderr(stderr))
        self.stderr = stderr

    def test_redirect_is_not_followed_and_the_bearer_goes_to_one_request(self):
        code, data = api.request("GET", "/api/v3/work_packages/1")
        self.assertEqual(code, 302)
        self.assertEqual(self.log, [("GET", "/api/v3/work_packages/1", "Bearer credential-sentinel")])

    def test_get_retries_a_real_503_then_succeeds(self):
        self.assertEqual(api.request("GET", "/api/v3/work_packages/2"), (200, {"id": 2}))
        self.assertEqual([entry[1] for entry in self.log], ["/api/v3/work_packages/2"] * 2)
        self.assertEqual(len(self.stderr.getvalue().splitlines()), 1)
        self.assertNotIn("credential-sentinel", self.stderr.getvalue())

    def test_post_gets_one_attempt_on_a_real_5xx(self):
        for method in ("POST", "PATCH"):
            self.log.clear()
            with self.assertRaisesRegex(api.ApiUnavailable, "unavailable"):
                api.request(method, "/api/v3/work_packages/3/activities", {"comment": {"raw": "x"}})
            self.assertEqual(len(self.log), 1)


class HelperSafetyTests(unittest.TestCase):
    def test_no_delete_and_only_project_api_paths(self):
        with self.assertRaises(AssertionError):
            api.request("DELETE", "/api/v3/work_packages/1")
        with self.assertRaises(AssertionError):
            api.request("GET", "/api/v3/users")  # not the allowlisted /users/me
        with self.assertRaises(AssertionError):
            api.request("GET", "/api/v3/work_packages/../x")

    def test_opwork_has_no_delete_command_and_no_token_output(self):
        text = (HERE / "opwork.py").read_text()
        self.assertNotIn('"DELETE"', text)
        self.assertNotIn("opapi-", text)
        self.assertNotIn("delete", op.build_parser().format_help().lower())


if __name__ == "__main__":
    unittest.main()
