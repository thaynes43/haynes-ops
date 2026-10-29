"""Shared agent CLI for the OpenProject backlog (project haynes-engineering).

Search first, claim, report progress, finish, create follow-ups. Standard library only,
usable by Claude Code and Codex alike, and built on agent-api.py (private API, token kept
in process memory, no delete). Nothing here is an approval gate: Tom and Meatball groom
scope, the routine lifecycle writes are yours. Run `opwork.py --help` for the commands.

  opwork.py search "text" [--status S] [--type T] [--parent ID] [--source-key K] [--all]
  opwork.py show 97 --history --relations
  opwork.py claim 97 --note "starting"          # Execution owner + In progress
  opwork.py comment 97 "progress..."            # attributed, read back
  opwork.py status 97 "In review" --note "PR #1"
  opwork.py complete 97 --evidence "tests ..."  # evidence comment + Accepted
  opwork.py create --epic 95 --origin 97 --source-key followup:97:x --subject "..."
  opwork.py checkpoint 97 "state..."            # explicit local record, no tracker needed
  opwork.py checkins [--owner OWNER] [--status S] -o json
  opwork.py checkpoint-update 97 --next-action TEXT --check-after UTC --evidence TEXT
  opwork.py pending | flush                     # tracker was down: list / replay notes

Every item line carries the canonical OpenProject link, for example
https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/97. OPWORK_BASE_URL
overrides that default (a plain https hostname: no credentials, port, path or query); opwork prints no other links.

Session identity (the Execution owner and the comment prefix) comes from OPWORK_SESSION,
else "<OPWORK_AGENT or agent> | worktree <name> | branch <branch>". Project defaults to
OPWORK_PROJECT or haynes-engineering. A mutation is reported done only after a read back.
Exit codes: 0 ok, 1 error, 3 conflict/refused (nothing overwritten), 4 needs a decision
(possible duplicate), 5 tracker unavailable (saved locally), 6 uncertain (verify first),
7 evidence recorded but Accepted is not allowed or the form re-check failed (read-back state reported).
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import uuid

PROJECT = "haynes-engineering"
BASE_URL = "https://work.haynesnetwork.com"  # the verified public address; the API origin in agent-api.py is cluster-internal
LINK_PROJECT = PROJECT  # main() sets it to --project, so the link names the project the command works in
LINK_BASE = BASE_URL  # main() sets it from OPWORK_BASE_URL after validating it, before any tracker request
BASE_RULE = "OPWORK_BASE_URL must be a plain https://hostname (ASCII letters, digits, hyphens and dots; no credentials, port, path or query)"
HOSTNAME = re.compile(r"https://(?=.{1,253}/?\Z)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*/?\Z", re.ASCII)
OWNER, REPOS, ACCEPT, CONSTRAINTS, SOURCE_KEY, PROVENANCE = "customField2", "customField3", "customField4", "customField5", "customField6", "customField11"
MARKDOWN = (ACCEPT, CONSTRAINTS, PROVENANCE)
HOLDS = ("Needs answer", "Needs approval", "Paused")  # an explicit hold or pause stays one without --force
RELATIONS = ("relates", "follows", "precedes", "blocks", "blocked")
REVERSE = {"follows": "precedes", "precedes": "follows", "blocks": "blocked", "blocked": "blocks", "relates": "relates"}
PARENT_OF = {"Epic": "Saga", "Story": "Epic"}


class Fail(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def load_api():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("agent_api", os.path.join(here, "agent-api.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def session_id():
    if os.environ.get("OPWORK_SESSION"):
        return os.environ["OPWORK_SESSION"]
    def git(*args):
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    top, branch = git("rev-parse", "--show-toplevel"), git("rev-parse", "--abbrev-ref", "HEAD")
    return "%s | worktree %s | branch %s" % (os.environ.get("OPWORK_AGENT", "agent"), os.path.basename(top) or "none", branch or "none")


def text_of(value):
    return (value or {}).get("raw", "") if isinstance(value, dict) else (value or "")


def href_id(link):
    href = (link or {}).get("href")
    return int(href.rsplit("/", 1)[-1]) if href else None


def base_url():
    """The validated link base. A strict match on the raw text, so nothing is parsed that could
    raise with the value in the message, and the value is never echoed (it may hold a secret)."""
    raw = os.environ.get("OPWORK_BASE_URL") or BASE_URL
    if not HOSTNAME.match(raw):
        raise Fail(BASE_RULE)
    return raw.rstrip("/")


def wp_url(wp_id):
    return "%s/projects/%s/work_packages/%s" % (LINK_BASE, LINK_PROJECT, wp_id)


def line(wp):
    parent = href_id(wp["_links"].get("parent"))
    owner = text_of(wp.get(OWNER))
    return "#%s %s [%s]%s lock=%s%s %s - %s" % (wp["id"], wp["_links"]["type"]["title"], wp["_links"]["status"]["title"],
                                                " parent=#%s" % parent if parent else "", wp["lockVersion"],
                                                " owner=<%s>" % owner if owner else "", wp_url(wp["id"]), wp["subject"])


class Tracker:
    """Thin typed layer over agent-api.request. `unavailable` is the exception it raises."""

    def __init__(self, request, unavailable, project=PROJECT):
        self.request, self.unavailable, self.project, self._names = request, unavailable, project, {}

    def call(self, method, path, payload=None):
        code, data = self.request(method, path, payload)
        return code, data if isinstance(data, dict) else {}

    def get(self, path):
        code, data = self.call("GET", path)
        if code >= 400:
            raise Fail("GET %s -> HTTP %s %s" % (path.split("?")[0], code, data.get("message", "")))
        return data

    def names(self, kind):  # name -> id for statuses / types, resolved live
        if kind not in self._names:
            self._names[kind] = {e["name"].lower(): e["id"] for e in self.get("/api/v3/" + kind)["_embedded"]["elements"]}
        return self._names[kind]

    def resolve(self, kind, name):
        ids = self.names(kind)
        if str(name).lower() not in ids:
            raise Fail("Unknown %s %r. Known: %s" % (kind[:-1], name, ", ".join(sorted(ids))))
        return ids[str(name).lower()]

    def wp(self, wp_id):
        return self.get("/api/v3/work_packages/%d" % int(wp_id))

    def listing(self, filters, page=1, size=20):
        query = urllib.parse.urlencode({"filters": json.dumps(filters), "pageSize": size, "offset": page, "sortBy": '[["id","asc"]]'})
        return self.get("/api/v3/projects/%s/work_packages?%s" % (self.project, query))

    def scan(self, filters):  # every page, for client-side matching (source keys are not server filters)
        rows, page = [], 1
        while True:
            data = self.listing(filters, page, 200)
            rows += data["_embedded"]["elements"]
            if len(rows) >= data["total"] or not data["_embedded"]["elements"]:
                return rows
            page += 1

    def allowed_statuses(self, wp):
        """Statuses the native workflow lets this role reach from the current one (form check, writes nothing)."""
        code, data = self.call("POST", "/api/v3/work_packages/%d/form" % wp["id"], {"lockVersion": wp["lockVersion"]})
        if code >= 400:
            raise Fail("Form check on #%d failed HTTP %s" % (wp["id"], code))
        values = data.get("_embedded", {}).get("schema", {}).get("status", {}).get("_embedded", {}).get("allowedValues")
        return None if values is None else [v["name"] for v in values]

    def activities(self, wp_id):
        return self.get("/api/v3/work_packages/%d/activities" % int(wp_id))["_embedded"]["elements"]

    def relations(self, wp_id):
        query = urllib.parse.urlencode({"filters": json.dumps([{"involved": {"operator": "=", "values": [str(wp_id)]}}])})
        return self.get("/api/v3/relations?" + query)["_embedded"]["elements"]

    def by_source_key(self, key):
        return [w for w in self.scan([{"status": {"operator": "*", "values": []}}]) if text_of(w.get(SOURCE_KEY)) == key]

    def patch(self, wp, fields=None, status=None, lock=None, assignee=None):
        """PATCH with lockVersion, then read back. Conflict re-reads and refuses; never overwrites."""
        fields = dict(fields or {})
        payload = {"lockVersion": wp["lockVersion"] if lock is None else lock}
        payload.update({k: ({"raw": v} if k in MARKDOWN else v) for k, v in fields.items()})
        links = {}
        if status:
            links["status"] = {"href": "/api/v3/statuses/%d" % self.resolve("statuses", status)}
        if assignee:
            links["assignee"] = {"href": "/api/v3/users/%d" % assignee}
        if links:
            payload["_links"] = links
        try:
            code, data = self.call("PATCH", "/api/v3/work_packages/%d" % wp["id"], payload)
        except self.unavailable:
            code, data = None, {}  # the write may or may not have landed: the read back decides
        if code == 409:
            now = self.wp(wp["id"])
            raise Fail("CONFLICT: #%d changed since it was read (lock %s -> %s). Not overwritten. Now: %s" % (wp["id"], payload["lockVersion"], now["lockVersion"], line(now)), 3)
        if code is not None and code >= 400:
            raise Fail("PATCH #%d rejected HTTP %s: %s" % (wp["id"], code, data.get("message", data.get("raw", ""))))
        now = self.wp(wp["id"])  # tracker still down: the caller reports unavailable
        ok = all(text_of(now.get(k)) == v for k, v in fields.items()) and (not status or now["_links"]["status"]["title"].lower() == status.lower()) \
            and (not assignee or (now["_links"].get("assignee") or {}).get("href") == "/api/v3/users/%d" % assignee)
        if not ok and code is None:
            raise Fail("NOT APPLIED: tracker error on the update of #%d; read back shows no change, safe to retry: %s" % (wp["id"], line(now)), 5)
        if not ok:
            raise Fail("UNCERTAIN: read back of #%d does not show the update: %s" % (wp["id"], line(now)), 6)
        return now

    def comment(self, wp_id, text, session):
        token = uuid.uuid4().hex[:12]
        body = "**[%s]** %s\n\n(opwork:note=%s)" % (session, text, token)
        return self.post_note(wp_id, body, token)

    def post_note(self, wp_id, body, token):
        def seen():
            return next((a for a in self.activities(wp_id) if "opwork:note=" + token in text_of(a.get("comment"))), None)
        try:
            code, data = self.call("POST", "/api/v3/work_packages/%d/activities" % wp_id, {"comment": {"raw": body}})
            if code >= 400:
                raise Fail("Comment on #%d rejected HTTP %s: %s" % (wp_id, code, data.get("message", "")))
            found = seen()  # success is the read back, not the 201
        except self.unavailable as down:
            down.token = token  # a later flush dedupes on it: the post may have landed
            raise
        if not found:
            raise Fail("UNCERTAIN: comment on #%d not visible on read back; `show %d --history` before repeating" % (wp_id, wp_id), 6)
        return found

    def find_relation(self, from_id, to_id, kind):
        """`from kind to`, as stored either way round: (from,to,kind) or (to,from,reverse kind)."""
        for rel in self.relations(from_id):
            ends = (href_id(rel["_links"]["from"]), href_id(rel["_links"]["to"]))
            if (ends, rel["type"]) in (((from_id, to_id), kind), ((to_id, from_id), REVERSE[kind])):
                return rel

    def relate(self, from_id, to_id, kind):
        rel = self.find_relation(from_id, to_id, kind)
        if rel:
            return rel, False
        code, data = self.call("POST", "/api/v3/work_packages/%d/relations" % from_id, {"type": kind, "_links": {"to": {"href": "/api/v3/work_packages/%d" % to_id}}})
        if code >= 400:
            raise Fail("Relation #%d %s #%d rejected HTTP %s: %s" % (from_id, kind, to_id, code, data.get("message", "")))
        rel = self.find_relation(from_id, to_id, kind)  # OpenProject may store follows/blocked in the canonical orientation
        if rel:
            return rel, True
        raise Fail("UNCERTAIN: relation #%d %s #%d not visible on read back" % (from_id, kind, to_id), 6)


# ---- commands: each returns printable text -----------------------------------------------

def cmd_ping(t, a, session):
    me = t.get("/api/v3/users/me")
    return "ok: %s (user %s), project %s, session <%s>" % (me["login"], me["id"], t.project, session)


def cmd_search(t, a, session):
    filters = []
    if a.status:
        filters.append({"status": {"operator": "=", "values": [str(t.resolve("statuses", s)) for s in a.status.split(",")]}})
    else:
        filters.append({"status": {"operator": "*" if a.all else "o", "values": []}})
    if a.type:
        filters.append({"type": {"operator": "=", "values": [str(t.resolve("types", a.type))]}})
    if a.parent:
        filters.append({"parent": {"operator": "=", "values": [str(a.parent)]}})
    if a.text:
        filters.append({"subjectOrId": {"operator": "**", "values": [a.text]}})
    if a.source_key or a.owner:  # not server filters: scan the filtered set and match here
        rows = [w for w in t.scan(filters) if (not a.source_key or text_of(w.get(SOURCE_KEY)) == a.source_key) and (not a.owner or a.owner.lower() in text_of(w.get(OWNER)).lower())]
        total, rows = len(rows), rows[(a.page - 1) * a.limit:a.page * a.limit]
    else:
        data = t.listing(filters, a.page, a.limit)
        total, rows = data["total"], data["_embedded"]["elements"]
    out = [line(w) for w in rows]
    out.append("-- %d of %d (page %d, %d per page%s)" % (len(rows), total, a.page, a.limit, "; use --page %d for more" % (a.page + 1) if a.page * a.limit < total else ""))
    return "\n".join(out)


# Check-in metadata is an attributed comment, not a workflow transition. The latest
# visible structured comment wins; an incomplete activity page never proves absence.
CHECKIN_MARKER = "opwork:checkin="


def utc_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def activity_page(t, wp_id):
    data = t.get("/api/v3/work_packages/%d/activities?pageSize=100&offset=1" % wp_id)
    rows = (data.get("_embedded") if isinstance(data.get("_embedded"), dict) else {}).get("elements")
    total = data.get("total")
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows) or not isinstance(total, int) or isinstance(total, bool) or total < len(rows):
        raise Fail("Malformed activity collection: elements or total missing/invalid")
    return rows, total <= len(rows)


def cmd_checkins(t, a, session):
    filters = [{"status": {"operator": "=", "values": [str(t.resolve("statuses", x)) for x in a.status.split(",")]}}] if a.status else [{"status": {"operator": "o", "values": []}}]
    data = t.listing(filters, a.page, a.limit)
    rows = (data.get("_embedded") if isinstance(data.get("_embedded"), dict) else {}).get("elements")
    total = data.get("total")
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows) or not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise Fail("Malformed work item collection: elements or total missing/invalid")
    now = datetime.now(timezone.utc)
    result = []
    for w in rows:
        owner = text_of(w.get(OWNER))
        if a.owner and a.owner.lower() not in owner.lower():
            continue
        acts, complete = activity_page(t, w["id"])
        candidates = [x for x in acts if CHECKIN_MARKER in text_of(x.get("comment"))]
        candidates.sort(key=lambda x: (utc_time(x.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc), x.get("id") if isinstance(x.get("id"), int) and not isinstance(x.get("id"), bool) else -1), reverse=True)
        meta, source, malformed = {}, None, False
        if candidates:
            act = candidates[0]
            try:
                if any(not utc_time(x.get("createdAt")) or not isinstance(x.get("id"), int) or isinstance(x.get("id"), bool) for x in candidates):
                    raise ValueError("invalid activity timestamp")
                comment = text_of(act.get("comment"))
                links = act.get("_links")
                user = links.get("user") if isinstance(links, dict) else None
                actor = re.fullmatch(r"/api/v3/users/([1-9][0-9]*)", user.get("href", "")) if isinstance(user, dict) and isinstance(user.get("href"), str) else None
                if not re.match(r"^\*\*\[[^]\n]+\]\*\* Structured checkpoint\n\n", comment) or not re.search(r"\(opwork:note=[0-9a-f]{12}\)", comment) or not utc_time(act.get("createdAt")) or not actor:
                    raise ValueError("invalid envelope")
                meta = json.loads(comment.split(CHECKIN_MARKER, 1)[1].split("\n", 1)[0])
                if not isinstance(meta, dict) or any(not isinstance(meta.get(k), str) or not meta[k].strip() for k in ("next_action", "check_after", "evidence")) or not utc_time(meta["check_after"]):
                    raise ValueError("invalid required fields")
                if any(meta.get(k) is not None and (not isinstance(meta[k], str) or not meta[k].strip()) for k in ("last_verified", "handoff")) or (meta.get("last_verified") and not utc_time(meta["last_verified"])) or any(meta.get(k) is not None and not isinstance(meta[k], bool) for k in ("ack", "stale", "blocked", "deployed")):
                    raise ValueError("invalid optional fields")
                source = {"activity_id": act.get("id"), "created_at": act.get("createdAt"), "author": user.get("title") or "user#%s" % actor.group(1)}
            except (ValueError, TypeError):
                meta, malformed = {}, True
        if not complete:
            meta = {}  # an unseen newer comment may supersede every visible value
        check = utc_time(meta.get("check_after"))
        status = w.get("_links", {}).get("status", {}).get("title") or "Unknown"
        reasons = []
        if check and check <= now: reasons.append("due")
        if meta.get("ack") is False: reasons.append("missing_ack")
        if meta.get("stale") is True: reasons.append("stale")
        if meta.get("blocked") is True or status in HOLDS: reasons.append("blocked")
        if meta.get("deployed") is True and status != "Accepted": reasons.append("deployed_not_accepted")
        if not reasons: reasons.append("unknown")
        result.append({"id": w["id"], "subject": w.get("subject"), "url": wp_url(w["id"]), "status": status, "owner": owner or "Unknown", "reasons": reasons,
                       "next_action": meta.get("next_action") or "Unknown", "check_after": meta.get("check_after") if check else "Unknown",
                       "evidence": meta.get("evidence") or "Unknown", "last_verified": meta.get("last_verified") or "Unknown", "handoff": meta.get("handoff") or "Unknown",
                       "provenance": text_of(w.get(PROVENANCE)) or "Unknown", "comment_source": source,
                       "metadata": "unknown" if not complete else ("malformed" if malformed else ("present" if candidates else "absent")), "history_complete": complete})
    return json.dumps({"items": result, "page": a.page, "page_size": a.limit, "total_before_owner_filter": data.get("total"), "has_more": a.page * a.limit < total, "owner_filter_scope": "selected page", "owner_match_total": "Unknown"}, sort_keys=True)


def cmd_checkpoint_update(t, a, session):
    w = t.wp(a.id)
    guard(w, session, False)
    if not a.next_action.strip() or not a.evidence.strip():
        raise Fail("next-action and evidence must not be empty")
    if a.handoff is not None and not a.handoff.strip():
        raise Fail("handoff must not be empty")
    if not utc_time(a.check_after):
        raise Fail("check-after must be an ISO 8601 timestamp with timezone")
    if a.last_verified is not None and not utc_time(a.last_verified):
        raise Fail("last-verified must be an ISO 8601 timestamp with timezone")
    meta = {"next_action": a.next_action, "check_after": a.check_after, "evidence": a.evidence, "last_verified": a.last_verified, "handoff": a.handoff,
            "ack": a.ack, "stale": a.stale, "blocked": a.blocked, "deployed": a.deployed}
    text = "Structured checkpoint\n\n" + CHECKIN_MARKER + json.dumps(meta, separators=(",", ":"), sort_keys=True)
    # Re-read before posting, refusing an owner/status race.
    refresh(t, w, None)
    token = uuid.uuid4().hex[:12]
    body = "**[%s]** %s\n\n(opwork:note=%s)" % (session, text, token)
    try:
        found = t.post_note(a.id, body, token)
    except t.unavailable:
        save_pending("structured_note", token, wp=a.id, session=session, text=text)
        raise
    except Fail as error:
        if error.code == 6:
            path, _ = save_pending("structured_note", token, wp=a.id, session=session, text=text)
            raise Fail("%s\nQueued locally: %s. Do not rerun checkpoint-update; `flush` deduplicates using the same token." % (error, path), 6)
        raise
    try:
        refresh(t, w, None)
    except t.unavailable as error:
        raise Fail("Checkpoint %s recorded on #%d (read back); final owner/status refresh unavailable: %s. Do not blindly retry." % (found.get("id"), a.id, error), 6)
    except Fail as error:
        raise Fail("Checkpoint %s recorded on #%d (read back); final refresh: %s. Do not blindly retry." % (found.get("id"), a.id, error), 6)
    return "checkpoint %s recorded on #%d (read back)" % (found.get("id"), a.id)


def clip(text, limit, full):
    return text if full or len(text) <= limit else "%s [truncated %d chars; use --full]" % (text[:limit], len(text) - limit)


def cmd_show(t, a, session):
    w = t.wp(a.id)
    out = [line(w), "  source-key: %s" % text_of(w.get(SOURCE_KEY)), "  repos: %s" % text_of(w.get(REPOS)),
           "  acceptance: %s" % text_of(w.get(ACCEPT)).replace("\n", "\n    "), "  constraints: %s" % text_of(w.get(CONSTRAINTS)).replace("\n", "\n    "),
           "  description: %s" % text_of(w.get("description")).replace("\n", "\n    ")]
    if a.relations:
        for r in t.relations(a.id):
            out.append("  relation: #%s %s #%s" % (href_id(r["_links"]["from"]), r["type"], href_id(r["_links"]["to"])))
    if a.history:
        for act in t.activities(a.id):
            who, when = act["_links"]["user"].get("title") or "user#%s" % href_id(act["_links"]["user"]), act["createdAt"]
            out.append("  [%s %s] %s %s" % (act.get("id"), when, who, clip(text_of(act.get("comment")).replace("\n", " "), 600, a.full)))
            out += ["      change: " + clip(text_of(d).replace("\n", " "), 200, a.full) for d in act.get("details", [])]
    return "\n".join(out)


def guard(w, session, force):
    status, owner = w["_links"]["status"]["title"], text_of(w.get(OWNER))
    if status in HOLDS and not force:
        raise Fail("REFUSED: #%d is [%s], an explicit hold. Wait for the answer/approval; --force only with explicit authorization." % (w["id"], status), 3)
    if owner and owner != session and not force:
        raise Fail("REFUSED: #%d is owned by <%s>, not this session. Not overwritten; coordinate or use --force (takeover)." % (w["id"], owner), 3)


def cmd_claim(t, a, session):
    w = t.wp(a.id)
    if w["_links"]["status"].get("title") == "Accepted":
        raise Fail("REFUSED: #%d is already Accepted (closed)" % w["id"], 3)
    guard(w, session, a.force)
    assignee = None
    if a.assignee:  # optional native assignee; never Tom automatically
        assignee = t.get("/api/v3/users/me")["id"] if a.assignee == "me" else int(a.assignee)
        if a.assignee != "me" and assignee not in [u["id"] for u in t.get("/api/v3/work_packages/%d/available_assignees" % w["id"])["_embedded"]["elements"]]:
            raise Fail("User %d is not an available assignee for #%d" % (assignee, w["id"]))
    now = t.patch(w, {OWNER: session}, "In progress", a.expect_lock, assignee)
    if a.note:
        t.comment(a.id, "Claimed. " + a.note, session)
    return "claimed (read back): " + line(now)


def cmd_comment(t, a, session):
    found = t.comment(a.id, a.text, session)
    return "comment %s recorded on #%d (read back)" % (found.get("id"), a.id)


def cmd_status(t, a, session):
    w = t.wp(a.id)
    guard(w, session, a.force)
    now = t.patch(w, status=a.state, lock=a.expect_lock)  # note only after the change took effect
    if a.note:
        t.comment(a.id, "Status -> %s. %s" % (a.state, a.note), session)
    return "status set (read back): " + line(now)


def refresh(t, w, expect_lock):
    """Re-read after our own note. Adopt a newer lock only if owner and status are unchanged; else refuse."""
    now = t.wp(w["id"])
    if (text_of(now.get(OWNER)), now["_links"]["status"]["title"]) != (text_of(w.get(OWNER)), w["_links"]["status"]["title"]):
        raise Fail("CONFLICT: #%d changed while this command ran (owner/status). Evidence note already posted; no status change made. Now: %s" % (w["id"], line(now)), 3)
    return w if expect_lock is not None else now


def cmd_complete(t, a, session):
    w = t.wp(a.id)
    if w["_links"]["status"]["title"] == "Accepted":
        return "already Accepted: " + line(w)
    guard(w, session, a.force)
    allowed = t.allowed_statuses(w)
    if allowed is not None and "Accepted" not in allowed:
        t.comment(a.id, "Ready for acceptance; evidence: " + a.evidence, session)
        now = refresh(t, w, None)  # display the current lock; the PATCH still enforces --expect-lock
        if now["_links"]["status"]["title"] != "In review" and "In review" in allowed:
            now = t.patch(now, status="In review", lock=a.expect_lock)
            try:
                allowed = t.allowed_statuses(now)  # the new state/lock can unlock the next transition
            except Fail as error:
                raise Fail("EVIDENCE RECORDED, NOT ACCEPTED: #%d is [In review]; form re-check failed: %s. Actual state after read back: %s. Do not re-run complete; check with `opwork show %d`." % (a.id, error, line(now), a.id), 7) from error
        if allowed is not None and "Accepted" not in allowed:
            raise Fail("EVIDENCE RECORDED, NOT ACCEPTED: Accepted is not allowed from the current state [%s]. Actual state after read back: %s. Allowed next statuses: %s. An authorized accepter may be needed." % (now["_links"]["status"]["title"], line(now), ", ".join(allowed) or "(none)"), 7)
        try:
            now = t.patch(now, status="Accepted")
        except Fail as error:
            if error.code == 3:
                raise Fail("EVIDENCE RECORDED: #%d was in [In review] after read back; acceptance conflicted. %s. Do not re-run complete; check with `opwork show %d`." % (a.id, error, a.id), 3) from error
            raise
    else:
        t.comment(a.id, "Completion evidence: " + a.evidence, session)  # first: a lost evidence note is never hidden by "already Accepted"
        now = t.patch(refresh(t, w, a.expect_lock), status="Accepted", lock=a.expect_lock)  # the lock may move if the note bumped it
    return "completed (read back): " + line(now)


def cmd_relate(t, a, session):
    rel, created = t.relate(a.src, a.dst, a.type)
    return "%s relation #%d %s #%d (read back)" % ("created" if created else "already present:", a.src, a.type, a.dst)


def cmd_create(t, a, session):
    existing = t.by_source_key(a.source_key)  # search first: the stable key is the identity
    if existing:
        out = ["REUSED, not created: " + "; ".join(line(w) for w in existing)]
        if a.origin and len(existing) == 1:  # a re-run completes a relation that was lost
            t.relate(existing[0]["id"], a.origin, "relates")
            out.append("related to #%d (read back)" % a.origin)
        return "\n".join(out)
    epic = t.wp(a.epic)
    kind = a.type
    if epic["_links"]["type"]["title"] != PARENT_OF[kind]:
        raise Fail("Grammar: a %s goes under a %s, but #%d is a %s" % (kind, PARENT_OF[kind], a.epic, epic["_links"]["type"]["title"]))
    words = [x for x in a.subject.lower().split() if len(x) > 3]
    similar = [w for w in t.scan([{"parent": {"operator": "=", "values": [str(a.epic)]}}, {"status": {"operator": "*", "values": []}}]) if words and sum(x in w["subject"].lower() for x in words) * 2 >= len(words)]
    if similar and not a.confirm_new:
        raise Fail("POSSIBLE DUPLICATE under #%d:\n  %s\nReuse one, or repeat with --confirm-new if this is genuinely separate work." % (a.epic, "\n  ".join(line(w) for w in similar)), 4)
    payload = {"subject": a.subject, "description": {"raw": a.description or ""}, SOURCE_KEY: a.source_key,
               PROVENANCE: {"raw": "Created by <%s>%s as a follow-up. Not groomed or approved for execution." % (session, " from #%d" % a.origin if a.origin else "")},
               "_links": {"type": {"href": "/api/v3/types/%d" % t.resolve("types", kind)}, "status": {"href": "/api/v3/statuses/%d" % t.resolve("statuses", "Ungroomed")},
                          "parent": {"href": "/api/v3/work_packages/%d" % a.epic}}}
    for field, value in ((REPOS, a.repos), (ACCEPT, a.acceptance), (CONSTRAINTS, a.constraints)):
        if value:
            payload[field] = {"raw": value} if field in MARKDOWN else value
    code, data, down = None, {}, None
    try:  # a create is never retried: on doubt, look by source key
        code, data = t.call("POST", "/api/v3/projects/%s/work_packages" % t.project, payload)
    except t.unavailable as error:
        down = error
    if code is not None and code >= 400:
        raise Fail("Create rejected HTTP %s: %s %s" % (code, data.get("message", ""), "; ".join(e.get("message", "") for e in data.get("_embedded", {}).get("errors", []))))
    made = t.by_source_key(a.source_key)
    if down and not made:
        raise down  # nothing was created; reported as tracker unavailable
    if len(made) != 1:
        raise Fail("UNCERTAIN: after the create, %d items carry source key %r. Do NOT repeat the create; inspect with `search --source-key`." % (len(made), a.source_key), 6)
    out = ["created (read back): " + line(made[0])]
    if a.origin:
        t.relate(made[0]["id"], a.origin, "relates")
        out.append("related to #%d (read back)" % a.origin)
    return "\n".join(out)


# ---- tracker down: explicit local record, no daemon --------------------------------------

def state_dir():
    path = os.path.join(os.environ.get("OPWORK_STATE_DIR") or os.path.expanduser("~/.local/state/opwork"), "pending")
    os.makedirs(path, exist_ok=True)
    return path


def save_pending(kind, token=None, **fields):
    record = dict(fields, kind=kind, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), token=token or uuid.uuid4().hex[:12])
    path = os.path.join(state_dir(), "%d-%s.json" % (time.time() * 1000, record["token"]))
    with open(path, "w") as handle:
        json.dump(record, handle, indent=1)
    return path, record


def cmd_checkpoint(t, a, session):
    path, _ = save_pending("note", wp=a.id, session=session, text=a.text)
    return "checkpoint saved locally (NOT in OpenProject yet): %s\nRun `flush` when the tracker is reachable." % path


def cmd_pending(t, a, session):
    rows = [json.load(open(os.path.join(state_dir(), f))) | {"file": f} for f in sorted(os.listdir(state_dir()))]
    return "\n".join("%s #%s %s %s" % (r["created"], r.get("wp", "-"), r["kind"], r.get("text") or " ".join(r.get("argv", []))) for r in rows) or "no pending records"


def cmd_flush(t, a, session):
    out = []
    for name in sorted(os.listdir(state_dir())):
        path = os.path.join(state_dir(), name)
        rec = json.load(open(path))
        if rec["kind"] not in ("note", "structured_note"):
            out.append("kept (re-run it yourself): %s" % " ".join(rec["argv"]))
            continue
        body = "**[%s]** Deferred checkpoint (written %s during a tracker outage): %s\n\n(opwork:note=%s)" % (rec["session"], rec["created"], rec["text"], rec["token"])
        if rec["kind"] == "structured_note":
            body = "**[%s]** %s\n\n(opwork:note=%s)" % (rec["session"], rec["text"], rec["token"])
        try:
            if rec["kind"] == "structured_note":
                w = t.wp(rec["wp"])
                guard(w, rec["session"], False)
                refresh(t, w, None)
                acts, complete = activity_page(t, rec["wp"])
                seen = any("opwork:note=" + rec["token"] in text_of(x.get("comment")) for x in acts)
                if not seen and not complete:
                    raise Fail("Incomplete history; cannot safely deduplicate checkpoint", 6)
                if not seen:
                    created = utc_time(rec.get("created"))
                    checkpoints = [x for x in acts if CHECKIN_MARKER in text_of(x.get("comment"))]
                    if not created or any(not utc_time(x.get("createdAt")) or utc_time(x.get("createdAt")) >= created for x in checkpoints):
                        raise Fail("Checkpoint may be superseded by newer or unordered history; retained for manual review", 6)
            else:
                seen = any("opwork:note=" + rec["token"] in text_of(x.get("comment")) for x in t.activities(rec["wp"]))
            if not seen:
                t.post_note(int(rec["wp"]), body, rec["token"])
        except Fail as failure:
            out.append("kept %s: %s" % (name, failure))
            continue
        os.remove(path)
        out.append("flushed #%s (read back)" % rec["wp"])
    return "\n".join(out) or "nothing pending"


def build_parser():
    p = argparse.ArgumentParser(prog="opwork", description=__doc__.split("\n\n")[0])
    p.add_argument("--project", default=os.environ.get("OPWORK_PROJECT", PROJECT))
    sub = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn, *args, **kw):
        s = sub.add_parser(name, help=kw.get("help"))
        for flags, opts in args:
            s.add_argument(*flags, **opts)
        s.set_defaults(fn=fn)
    wp = (("id",), {"type": int})
    lock = (("--expect-lock",), {"type": int, "help": "lockVersion you read earlier; stale = conflict"})
    force = (("--force",), {"action": "store_true", "help": "override a hold/ownership refusal (explicit authorization only)"})
    add("ping", cmd_ping, help="check the tracker, show the session identity")
    add("search", cmd_search, (("text",), {"nargs": "?"}), (("--status",), {}), (("--type",), {}), (("--parent",), {"type": int}), (("--source-key",), {}), (("--owner",), {}),
        (("--all",), {"action": "store_true", "help": "include closed"}), (("--page",), {"type": int, "default": 1}), (("--limit",), {"type": int, "default": 20}), help="search work items")
    add("checkins", cmd_checkins, (("--owner",), {}), (("--status",), {}), (("-o", "--output"), {"choices": ["json"], "required": True}), (("--page",), {"type": int, "default": 1}), (("--limit",), {"type": int, "default": 100}), help="bounded check-in view; owner filter applies to selected page, use has_more for global continuation")
    add("checkpoint-update", cmd_checkpoint_update, wp, (("--next-action",), {"required": True}), (("--check-after",), {"required": True}), (("--evidence",), {"required": True}), (("--last-verified",), {}), (("--handoff",), {}), (("--ack",), {"action": "store_true", "default": None}), (("--missing-ack",), {"dest": "ack", "action": "store_false"}), (("--stale",), {"action": "store_true", "default": None}), (("--blocked",), {"action": "store_true", "default": None}), (("--deployed",), {"action": "store_true", "default": None}), help="post attributed structured checkpoint")
    add("show", cmd_show, wp, (("--history",), {"action": "store_true"}), (("--relations",), {"action": "store_true"}), (("--full",), {"action": "store_true", "help": "no truncation of history text"}), help="read one item (read-only)")
    add("claim", cmd_claim, wp, (("--note",), {}), (("--assignee",), {"help": "also set the native assignee: me, or a user id available on the item"}), lock, force, help="set Execution owner to this session, In progress")
    add("comment", cmd_comment, wp, (("text",), {"help": "or - to read stdin"}), help="attributed progress note")
    add("status", cmd_status, wp, (("state",), {}), (("--note",), {}), lock, force, help="explicit status change")
    add("complete", cmd_complete, wp, (("--evidence",), {"required": True}), lock, force, help="evidence comment, then Accepted")
    add("relate", cmd_relate, (("src",), {"type": int}), (("dst",), {"type": int}), (("--type",), {"default": "relates", "choices": RELATIONS}), help="link two items")
    add("create", cmd_create, (("--epic",), {"type": int, "required": True}), (("--subject",), {"required": True}), (("--source-key",), {"required": True, "help": "stable key, e.g. followup:97:topic"}),
        (("--origin",), {"type": int, "help": "originating Story (relates link)"}), (("--type",), {"default": "Story", "choices": list(PARENT_OF)}), (("--description",), {}), (("--repos",), {}),
        (("--acceptance",), {}), (("--constraints",), {}), (("--confirm-new",), {"action": "store_true"}), help="search-first follow-up, filed Ungroomed")
    add("checkpoint", cmd_checkpoint, wp, (("text",), {}), help="save a note locally (works offline)")
    add("pending", cmd_pending, help="list locally saved records")
    add("flush", cmd_flush, help="post saved notes to the tracker, with dedupe and read back")
    return p


def main(argv=None, tracker=None, unavailable=None):
    global LINK_PROJECT, LINK_BASE
    args = build_parser().parse_args(argv)
    LINK_PROJECT = args.project
    if args.cmd == "checkins" and (args.page < 1 or not 1 <= args.limit <= 100):
        print("page must be positive and limit must be 1..100", file=sys.stderr)
        return 1
    session = session_id()
    if getattr(args, "text", None) == "-":
        args.text = sys.stdin.read().strip()
    if args.fn in (cmd_checkpoint, cmd_pending):
        tracker = None
    try:
        if args.fn not in (cmd_checkpoint, cmd_pending):  # these print no item lines; validate before any tracker object or request
            LINK_BASE = base_url()
        if tracker is None and args.fn not in (cmd_checkpoint, cmd_pending):
            api = load_api()
            tracker, unavailable = Tracker(api.request, api.ApiUnavailable, args.project), api.ApiUnavailable
        print(args.fn(tracker, args, session))
        return 0
    except Fail as failure:
        print(failure, file=sys.stderr)
        return failure.code
    except (unavailable or ()) as down:
        if args.fn is cmd_checkpoint_update and hasattr(down, "token"):
            print("TRACKER UNAVAILABLE: structured checkpoint queued locally; write is unconfirmed and flush deduplicates by token.", file=sys.stderr)
            return 5
        mutating = args.fn in (cmd_claim, cmd_comment, cmd_status, cmd_complete, cmd_create, cmd_relate)
        if args.fn is cmd_comment:
            path, rec = save_pending("note", getattr(down, "token", None), wp=args.id, session=session, text=args.text)
        elif mutating:
            path, rec = save_pending("intent", wp=getattr(args, "id", None), session=session, argv=sys.argv[1:])
        else:
            path = None
        if not mutating:
            print("TRACKER UNAVAILABLE: %s. The read failed; authorized bounded work can continue." % down, file=sys.stderr)
        else:
            print("TRACKER UNAVAILABLE: %s.\nWhether the write reached OpenProject is UNCONFIRMED. Saved locally: %s\n%s\nAuthorized bounded work can continue." % (
                down, path, "`flush` posts it unless it already landed (deduped by token)." if args.fn is cmd_comment else "When the tracker is back, `search`/`show` the item, then re-run the command only if the change is missing."), file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
