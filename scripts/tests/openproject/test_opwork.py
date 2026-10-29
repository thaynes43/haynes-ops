"""Offline tests for scripts/openproject/opwork.py against an in-memory fake OpenProject.

Mocked/fixture tests: they prove the helper's logic (claims, conflicts, attribution,
pagination, uncertain create, outage handling), NOT a live cross-harness acceptance run.
No network, kubectl or credentials. Run: python3 -I -B scripts/tests/openproject/test_opwork.py -v
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
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
        self.deny = set()  # statuses the role may not reach (the live workflow denies Accepted)
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
                names = [n for n in STATUSES if n not in self.deny]
                return 200, {"_embedded": {"schema": {"status": {"_embedded": {"allowedValues": [{"name": n} for n in names]}}}}}
            if sub is None and method == "PATCH":
                if payload["lockVersion"] != wp["lockVersion"]:
                    return 409, {"errorIdentifier": "UpdateConflict"}
                target = self.hrefid(payload.get("_links", {}).get("status", {"href": "/0"})["href"])
                if any(STATUSES[n] == target for n in self.deny):
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
                wp["lockVersion"] += 1
                return 200, wp
            if sub == "/activities" and method == "GET":
                return 200, {"total": len(self.acts[i]), "_embedded": {"elements": self.acts[i]}}
            if sub == "/activities" and method == "POST":
                self.next_act += 1
                self.acts[i].append({"id": self.next_act, "createdAt": "2026-10-29T00:00:00Z", "comment": payload["comment"], "details": [], "_links": {"user": {"title": "openproject-agent"}}})
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
        self.story(11, status="Needs approval")
        for i in (10, 11):
            self.assertEqual(self.run_cli("complete", str(i), "--evidence", "x")[0], 3)
            self.assertEqual(self.fake.acts[i], [])

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
        self.assertEqual(self.posts("/activities"), [])


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
        self.assertIn("Deferred checkpoint", self.fake.acts[10][0]["comment"]["raw"])
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
