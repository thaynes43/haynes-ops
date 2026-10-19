"""Offline tests for scripts/openproject/opwork.py against an in-memory fake OpenProject.

Mocked/fixture tests: they prove the helper's logic (claims, conflicts, attribution,
pagination, uncertain create, outage handling), NOT a live cross-harness acceptance run.
No network, kubectl or credentials. Run: python3 -I -B scripts/tests/openproject/test_opwork.py -v
"""
import contextlib
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
                return 200, wp
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
                return 200, {"_embedded": {"elements": self.acts[i]}}
            if sub == "/activities" and method == "POST":
                self.next_act += 1
                self.acts[i].append({"id": self.next_act, "createdAt": "t", "comment": payload["comment"], "details": [], "_links": {"user": {"title": "openproject-agent"}}})
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
