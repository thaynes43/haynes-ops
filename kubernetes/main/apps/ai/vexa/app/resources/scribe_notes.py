#!/usr/bin/env python3
"""scribe-notes worker — meeting-scribe/03 §4-§6.

One completed ΣΦΟ Scribe capture -> a reviewed wiki page, mechanically:

  gather -> summarize (claude -p, CLI-first / API-fallback, shepherd pattern)
         -> validate -> compose (deterministic §5 skeleton) -> [publish] -> notify

Principles (design §1): code decides structure, the LLM only writes prose;
idempotent at every seam; fail loud, publish nothing wrong.

Runs as a Job in ns `ai` (the dispatcher creates one per completed meeting) OR
locally for a dry run — set SCRIBE_NOTES_TRANSCRIPT_FILE (+ optional
SCRIBE_NOTES_META_FILE) to feed a saved transcript instead of the gateway/feed.
Publishing and notifying are OFF by default (SCRIBE_NOTES_PUBLISH,
SCRIBE_NOTES_NOTIFY) so a deployed-but-unsupervised run composes without touching
the wiki or the board mailbox — the first real publish is a supervised self-test.

Env:
  MEET_CODE                 Meet join code (required unless a transcript file is given)
  MEETING_DB_ID             Vexa meeting id (job name / dedupe; informational here)
  EVENT_ID                  site event id to match in the completed-meetings feed
  SITE_ORIGIN               e.g. https://frontpage-...run.app  (feed + attendance + notify)
  GATEWAY                   e.g. http://vexa-vexa-gateway:8000  (transcript)
  SCRIBE_DISPATCH_TOKEN     bearer for the site internal endpoints
  VEXA_API_KEY              X-API-Key for the gateway
  SCRIBE_NOTES_MODEL        claude model (default: fable — board-facing prose)
  SCRIBE_NOTES_PUBLISH      "true" to write to Outline (default: false -> compose only)
  SCRIBE_NOTES_NOTIFY       "true" to send publish/failure notifications (default: false)
  SCRIBE_NOTES_OUT          where to write the composed page (default: /tmp/scribe-notes.md)
  OUTLINE_API_URL           e.g. https://wiki.sigoalumni.org/api
  OUTLINE_API_TOKEN         Outline "scribe-notes" token (publish only)
  PUSHOVER_TOKEN / PUSHOVER_USER_KEY   Pushover (notify only; optional)
  CLAUDE_CODE_OAUTH_TOKEN   Max-plan token (preferred)  ── shepherd auth pattern
  ANTHROPIC_API_KEY         metered fallback key
  SCRIBE_NOTES_TRANSCRIPT_FILE / SCRIBE_NOTES_META_FILE   dry-run local inputs
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - py<3.9
    ZoneInfo = None

BOT_NAMES = {"σφο scribe", "sigo notes"}
GENERIC_SPEAKERS = {"speaker", "unknown", ""}
PROMPT_FILE = os.environ.get("SCRIBE_NOTES_PROMPT", "/app/summarize-prompt.md")


def log(*a):
    print(f"{datetime.now(timezone.utc).isoformat()}", *a, file=sys.stderr, flush=True)


class Fail(Exception):
    """A stage failed; the run notifies (failure) and exits non-zero."""

    def __init__(self, stage, msg):
        super().__init__(msg)
        self.stage = stage


# ── HTTP ─────────────────────────────────────────────────────────────────────
def http(url, headers=None, data=None, method=None, timeout=30):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── Gather ───────────────────────────────────────────────────────────────────
def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load_meta(code, event_id):
    """Site metadata for this meeting from the completed-meetings feed, or a
    local file for a dry run."""
    mf = os.environ.get("SCRIBE_NOTES_META_FILE")
    if mf:
        with open(mf) as f:
            return json.load(f)
    site = os.environ["SITE_ORIGIN"].rstrip("/")
    token = os.environ["SCRIBE_DISPATCH_TOKEN"]
    st, body = http(
        f"{site}/api/internal/completed-meetings?windowHours=720",
        {"Authorization": f"Bearer {token}"},
    )
    if st != 200:
        raise Fail("gather", f"completed-meetings feed HTTP {st}")
    meetings = json.loads(body).get("meetings", [])
    for m in meetings:
        if (event_id and m.get("id") == event_id) or m.get("code") == code:
            return m
    raise Fail("gather", f"no site event matched code={code} event_id={event_id}")


def load_transcript(code):
    """Normalized transcript segments + stats from the gateway, or a local file."""
    tf = os.environ.get("SCRIBE_NOTES_TRANSCRIPT_FILE")
    if tf:
        with open(tf) as f:
            raw = json.load(f)
        segs = raw.get("segments", [])
        start = parse_iso(raw.get("start_time"))
        end = parse_iso(raw.get("end_time"))
    else:
        gw = os.environ["GATEWAY"].rstrip("/")
        st, body = http(
            f"{gw}/transcripts/google_meet/{code}",
            {"X-API-Key": os.environ["VEXA_API_KEY"]},
        )
        if st != 200:
            raise Fail("gather", f"transcript fetch HTTP {st}")
        raw = json.loads(body)
        segs = raw.get("segments", [])
        start = parse_iso(raw.get("start_time"))
        end = parse_iso(raw.get("end_time"))

    segments = []
    for s in segs:
        segments.append(
            {
                "speaker": s.get("speaker"),
                "text": (s.get("text") or "").strip(),
                "startAt": s.get("absolute_start_time") or s.get("startAt"),
                "endAt": s.get("absolute_end_time") or s.get("endAt"),
                "segment_id": s.get("segment_id") or s.get("id"),
            }
        )
    dur_min = 0
    if start and end:
        dur_min = max(0, round((end - start).total_seconds() / 60))
    seg_per_min = (len(segments) / dur_min) if dur_min else 0.0
    stats = {
        "durationMin": dur_min,
        "segmentCount": len(segments),
        "segPerMin": round(seg_per_min, 3),
        "start": start,
        "end": end,
    }
    return segments, stats


def load_attendance(code):
    """Tier-2 present-silent participants from the site attendance endpoint.
    None = unavailable (stated honestly on the page); [] = record exists, nobody
    silent."""
    if os.environ.get("SCRIBE_NOTES_TRANSCRIPT_FILE") and not os.environ.get("SITE_ORIGIN"):
        return None  # pure local dry run, no site reachable
    site = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    token = os.environ.get("SCRIBE_DISPATCH_TOKEN")
    if not site or not token:
        return None
    st, body = http(
        f"{site}/api/internal/meeting-attendance?code={code}",
        {"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if st != 200:
        log(f"attendance endpoint HTTP {st}; treating as unavailable")
        return None
    payload = json.loads(body)
    att = payload.get("attendance")
    if not att:
        return None
    return att.get("participants", [])


def is_bot(name):
    return (name or "").strip().lower() in BOT_NAMES


def tier1_speakers(segments):
    """Distinct real speakers who spoke (present-and-spoke), bot + generic
    attributions excluded, first-appearance order."""
    seen, out = set(), []
    for s in segments:
        nm = (s.get("speaker") or "").strip()
        key = nm.lower()
        if not nm or key in GENERIC_SPEAKERS or is_bot(nm) or key in seen:
            continue
        seen.add(key)
        out.append(nm)
    return out


def had_generic_speaker(segments):
    """True if any real content was attributed only to a generic 'Speaker' —
    the page discloses that attribution was partial."""
    return any((s.get("speaker") or "").strip().lower() in GENERIC_SPEAKERS for s in segments)


# ── Summarize (claude -p, CLI-first / API-fallback — shepherd pattern) ─────────
def _run_claude(model, prompt, use_api):
    """One claude -p run. Returns (rc, combined_output_text)."""
    env = dict(os.environ)
    for k in (
        "DISABLE_TELEMETRY",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_AUTOUPDATER",
        "DISABLE_NON_ESSENTIAL_MODEL_CALLS",
    ):
        env[k] = "1"
    env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "0"
    if use_api:
        # Force the metered key: claude-code prefers ANTHROPIC_API_KEY when both
        # are set, so simply keeping it in env is enough — but drop the plan token
        # so the path is unambiguous.
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    else:
        # Plan path: unset the API key so a $0 plan run isn't silently billed.
        env.pop("ANTHROPIC_API_KEY", None)
    args = [
        "claude",
        "-p",
        prompt,
        "--permission-mode",
        "dontAsk",
        "--disallowedTools",
        "WebFetch",
        "WebSearch",
        "--model",
        model,
        "--output-format",
        "json",
    ]
    try:
        p = subprocess.run(
            args, env=env, capture_output=True, text=True, timeout=600
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _extract_json_object(text):
    """Pull the model's JSON object out of the claude --output-format json
    envelope. The envelope's `.result` is the model's text (our prompt asks for a
    bare JSON object); fall back to the raw text if it isn't an envelope."""
    result = text
    try:
        env = json.loads(text)
        if isinstance(env, dict) and "result" in env:
            result = env["result"]
    except Exception:
        pass
    result = result.strip()
    # Tolerate a stray ```json fence.
    if result.startswith("```"):
        result = result.split("\n", 1)[-1]
        if result.endswith("```"):
            result = result.rsplit("```", 1)[0]
    # Grab the outermost {...}.
    i, j = result.find("{"), result.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        return json.loads(result[i : j + 1])
    except Exception:
        return None


def summarize(meta, segments):
    """Drive claude -p with the pinned prompt; return the parsed summary object.
    Plan-first, metered-API fallback on rate-limit/auth failure (shepherd)."""
    with open(PROMPT_FILE) as f:
        pinned = f.read()
    payload = {
        "meeting": {
            "title": meta.get("title"),
            "timezone": meta.get("timezone"),
            "startAt": meta.get("startAt"),
            "endAt": meta.get("endAt"),
            "description": meta.get("description"),
        },
        "segments": [
            {
                "speaker": s["speaker"],
                "text": s["text"],
                "startAt": s["startAt"],
                "segment_id": s["segment_id"],
            }
            for s in segments
        ],
    }
    prompt = pinned + "\n\n# INPUT\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"

    have_plan = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    have_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
    # Dry runs against a saved transcript use whatever credential the local
    # `claude` already holds (the dev-env pod's file credential) — no env token.
    local_cred = bool(os.environ.get("SCRIBE_NOTES_TRANSCRIPT_FILE"))
    if not have_plan and not have_api and not local_cred:
        raise Fail("summarize", "no CLAUDE_CODE_OAUTH_TOKEN and no ANTHROPIC_API_KEY")
    model = os.environ.get("SCRIBE_NOTES_MODEL", "fable")

    def attempt(use_api):
        auth = "api" if use_api else "plan"
        log(f"summarize: claude -p model={model} auth={auth}")
        rc, out = _run_claude(model, prompt, use_api)
        obj = _extract_json_object(out) if rc == 0 else None
        return rc, out, obj

    # Primary: plan path if a plan token is mounted, else the metered API key.
    use_api = not have_plan
    rc, out, obj = attempt(use_api)

    # Fallback A (shepherd): a plan run that failed for a rate-limit/auth reason
    # falls back to the metered API key ONCE. After this, `use_api` is the live path.
    if obj is None and not use_api and have_api:
        low = out.lower()
        rate = ("rate limit", "usage limit", "limit reached", "too many requests", "429")
        auth = ("unauthorized", "invalid api key", "invalid token", "authentication", "401", "/login")
        if any(k in low for k in rate) or any(k in low for k in auth):
            log("summarize: plan path failed (rate-limit/auth) — falling back to the metered API key")
            use_api = True
            rc, out, obj = attempt(use_api)

    # Fallback B (§4): one retry on invalid/empty JSON, on the live path.
    if obj is None:
        log("summarize: no valid JSON — one retry on the live path")
        rc, out, obj = attempt(use_api)

    if obj is None:
        raise Fail("summarize", f"claude produced no valid JSON (rc={rc})")
    # Normalize shape.
    obj.setdefault("motions", [])
    obj.setdefault("topics", [])
    obj.setdefault("actionItems", [])
    obj.setdefault("nextMeeting", None)
    obj.setdefault("callToOrder", None)
    obj.setdefault("adjournedAt", None)
    return obj


# ── Validate (composer, in code — §4/§9) ──────────────────────────────────────
def validate(summary, segments, tier1, tier2, invited_names):
    """Enforce what code can check: every motion cites real segment_ids; unknown
    action-item owners are flagged, not named."""
    seg_ids = {s["segment_id"] for s in segments if s.get("segment_id")}
    known = {n.lower() for n in tier1}
    known |= {p.get("displayName", "").lower() for p in (tier2 or [])}
    known |= {n.lower() for n in invited_names}

    for m in summary["motions"]:
        refs = m.get("segmentRefs") or []
        if not refs or not all(r in seg_ids for r in refs):
            # No fabricated votes, ever (§4): an uncited motion fails the run.
            raise Fail("validate", f"motion cites unknown segmentRefs {refs!r}")

    for it in summary["actionItems"]:
        owner = (it.get("owner") or "").strip()
        if owner and owner.lower() not in known:
            it["ownerUnverified"] = True  # composer renders a TODO-verify-data callout
    return summary


# ── Compose (deterministic §5 skeleton) ───────────────────────────────────────
def _zone(tz):
    if tz and ZoneInfo:
        try:
            return ZoneInfo(tz)
        except Exception:
            return None
    return None


def _fmt_time(dt, zone):
    if not dt:
        return None
    local = dt.astimezone(zone) if zone else dt
    # "8:00 PM" without a leading zero, portable (no %-I on all libc).
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _tz_label(tz, dt, zone):
    if tz == "America/New_York":
        return "ET"
    if zone and dt:
        return dt.astimezone(zone).strftime("%Z") or (tz or "")
    return tz or ""


def _date_str(dt, zone):
    if not dt:
        return "(date unknown)"
    local = dt.astimezone(zone) if zone else dt
    return local.strftime("%Y-%m-%d")


def render_invited(audience):
    """Tier-3 line from the feed audience: named list for a custom list, a
    labelled distribution otherwise; never a name dump for a group."""
    if not audience:
        return "not recorded for this meeting"
    invited = audience.get("invited") or []
    label = audience.get("label")
    count = audience.get("invitedCount")
    if invited:
        names = " · ".join(i.get("name") or i.get("email") for i in invited)
        return names
    if label:
        return f"{label} ({count} addresses)" if isinstance(count, int) else label
    if isinstance(count, int):
        return f"{count} addresses"
    return "not recorded for this meeting"


def compose(meta, summary, tier1, tier2, attendance_known, generic_present, stats):
    """Fill the §5 template deterministically. Notes vs minutes is decided HERE
    by the motion count — the LLM never chooses the label (§9)."""
    tz = meta.get("timezone")
    zone = _zone(tz)
    start = parse_iso(meta.get("startAt")) or stats.get("start")
    end = parse_iso(meta.get("endAt")) or stats.get("end")
    date = _date_str(start, zone)
    is_minutes = len(summary["motions"]) > 0
    label = "minutes" if is_minutes else "notes"
    title = f"{date} — {meta.get('title') or 'Meeting'} ({label})"

    t1 = _fmt_time(start, zone)
    t2 = _fmt_time(end, zone)
    tzl = _tz_label(tz, start, zone)
    if t1 and t2:
        # Collapse a shared meridiem: "8:00–8:47 PM ET".
        if t1[-2:] == t2[-2:]:
            time_str = f"{t1[:-3]}–{t2} {tzl}".replace("  ", " ")
        else:
            time_str = f"{t1} – {t2} {tzl}"
    else:
        time_str = "(time unknown)"
    dur = stats.get("durationMin") or 0

    L = []
    L.append(f"# {title}")
    L.append("")
    L.append(":::warning")
    L.append(
        "**Pending review** — generated from the ΣΦΟ Scribe transcript. A board member "
        "verifies attendance and content, then removes this banner."
    )
    L.append(":::")
    L.append("")
    L.append(
        f"**Date:** {date} **Time:** {time_str} **Location:** Google Meet "
        "(scheduled through sigoalumni.org) **Recording:** captured by ΣΦΟ Scribe; "
        f"audio retained on org infrastructure ({dur} min)"
    )
    L.append("")
    if not is_minutes:
        L.append(
            "*No motions were made and no votes were taken. These notes summarize "
            "discussion; they are not minutes for approval.*"
        )
        L.append("")

    # Attendance (three labeled tiers, each naming its source; missing tiers stated).
    L.append("## Attendance")
    L.append("")
    if tier1:
        spoke = " · ".join(tier1)
    else:
        spoke = "*speaker attribution unavailable for this meeting*"
    L.append(f"**Present (spoke in the transcript):** {spoke}")
    if not attendance_known:
        silent = "not captured for this meeting (participant log unavailable)"
    else:
        names = [p.get("displayName") for p in (tier2 or []) if p.get("displayName")]
        silent = " · ".join(names) if names else "none recorded (everyone who joined also spoke)"
    L.append(f"**Present (joined without speaking):** {silent}")
    L.append(f"**Invited:** {render_invited(meta.get('audience'))}")
    if generic_present:
        L.append("")
        L.append(
            "*Some remarks could not be attributed to a named speaker.*"
        )
    L.append("")
    L.append(
        "*Sources: transcript speaker attribution; Meet participant log; announcement "
        "roster. Absence is not inferred — someone missing above may still have attended.*"
    )
    L.append("")

    if is_minutes and summary.get("callToOrder"):
        L.append(f"**Called to order:** {summary['callToOrder']}")
        L.append("")

    # Discussion.
    L.append("## Discussion")
    L.append("")
    topics = summary.get("topics") or []
    if not topics:
        L.append("*No substantive discussion was captured in the transcript.*")
        L.append("")
    for t in topics:
        L.append(f"### {t.get('heading') or 'Discussion'}")
        L.append((t.get("summary") or "").strip())
        L.append("")

    # Motions (minutes only).
    if is_minutes:
        L.append("## Motions")
        L.append("")
        for m in summary["motions"]:
            L.append(f"- **Motion:** {m.get('text')}")
            meta_bits = []
            if m.get("movedBy"):
                meta_bits.append(f"moved by {m['movedBy']}")
            if m.get("secondedBy"):
                meta_bits.append(f"seconded by {m['secondedBy']}")
            if m.get("vote"):
                meta_bits.append(f"vote {m['vote']}")
            if m.get("outcome"):
                meta_bits.append(str(m["outcome"]))
            if meta_bits:
                L.append(f"  {'; '.join(meta_bits)}.")
            if m.get("bindsCorporation"):
                L.append(f"  Binds the **{m['bindsCorporation']}**.")
        L.append("")

    # Action items.
    L.append("## Action items")
    L.append("")
    L.append("| Owner | Action | Due |")
    L.append("|-------|--------|-----|")
    for it in summary.get("actionItems") or []:
        if it.get("ownerUnverified") or not it.get("owner"):
            owner = ":::warning TODO-verify-data: owner not confirmed:::"
            # Inline callouts don't render in a table cell; keep it plain + flag.
            owner = "TODO-verify-data (owner unconfirmed)"
        else:
            owner = it["owner"]
        L.append(f"| {owner} | {it.get('action','')} | {it.get('due') or ''} |")
    L.append("")

    # Next meeting.
    L.append("## Next meeting")
    L.append("")
    L.append(summary.get("nextMeeting") or "*Not discussed.*")
    L.append("")

    if is_minutes:
        L.append("---")
        L.append("*Submitted by ΣΦΟ Scribe (machine draft) — pending Secretary review.*")
        L.append("")
    L.append("---")
    L.append(
        "*Captured by ΣΦΟ Scribe; summarized from the full transcript. Event details "
        "from sigoalumni.org.*"
    )
    return title, "\n".join(L)


# ── Publish (Outline REST; gated) ─────────────────────────────────────────────
def publish(title, markdown):
    url = os.environ.get("OUTLINE_API_URL", "https://wiki.sigoalumni.org/api").rstrip("/")
    token = os.environ.get("OUTLINE_API_TOKEN")
    if not token:
        raise Fail("publish", "OUTLINE_API_TOKEN not set")
    # NOTE: parent placement (Meetings -> <type> -> <year>) + pre-publish
    # existence check are wired in the supervised-publish step; phase-1 publish is
    # gated off, so this is the minimal create used by the first supervised test.
    body = json.dumps(
        {"title": title, "text": markdown, "publish": True, "collectionId": os.environ.get("OUTLINE_COLLECTION_ID")}
    ).encode()
    st, resp = http(
        f"{url}/documents.create",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        body,
        "POST",
    )
    if st not in (200, 201):
        raise Fail("publish", f"documents.create HTTP {st}")
    data = json.loads(resp)
    doc_url = data.get("data", {}).get("url")
    return f"{url.replace('/api','')}{doc_url}" if doc_url else url


# ── Notify (Pushover worker-side + email via the site) ────────────────────────
def notify(kind, title, page_url=None, stage=None):
    if os.environ.get("SCRIBE_NOTES_NOTIFY", "false").lower() != "true":
        log(f"notify ({kind}) suppressed (SCRIBE_NOTES_NOTIFY!=true): {title} url={page_url} stage={stage}")
        return
    # Pushover (independent loud channel; optional).
    ptok = os.environ.get("PUSHOVER_TOKEN")
    puser = os.environ.get("PUSHOVER_USER_KEY")
    if kind == "published":
        msg = f"Meeting notes published: {title}" + (f" — {page_url}" if page_url else "")
    else:
        msg = f"Notes pipeline failed at {stage} for {title}"
    if ptok and puser:
        try:
            data = urllib.parse.urlencode(
                {"token": ptok, "user": puser, "message": msg, "title": "ΣΦΟ Scribe"}
            ).encode()
            st, _ = http("https://api.pushover.net/1/messages.json", None, data, "POST", timeout=15)
            log(f"pushover {kind}: HTTP {st}")
        except Exception as e:
            log(f"pushover failed: {e}")
    else:
        log("pushover creds not set — skipping (email is the other channel)")
    # Email via the site's branded shell.
    site = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    token = os.environ.get("SCRIBE_DISPATCH_TOKEN")
    if site and token:
        payload = {"kind": kind, "title": title}
        if page_url:
            payload["url"] = page_url
        if stage:
            payload["stage"] = stage
        try:
            st, _ = http(
                f"{site}/api/internal/scribe-notify",
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json.dumps(payload).encode(),
                "POST",
                timeout=30,
            )
            log(f"email notify {kind}: HTTP {st}")
        except Exception as e:
            log(f"email notify failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    code = os.environ.get("MEET_CODE", "")
    event_id = os.environ.get("EVENT_ID", "")
    out_path = os.environ.get("SCRIBE_NOTES_OUT", "/tmp/scribe-notes.md")
    title = code or "meeting"
    try:
        meta = load_meta(code, event_id)
        code = code or meta.get("code") or ""
        title = meta.get("title") or code

        segments, stats = load_transcript(code)
        log(f"gather: {stats['segmentCount']} segments, {stats['durationMin']} min, {stats['segPerMin']} seg/min")
        if stats["segmentCount"] == 0:
            # No page beats a wrong page (§8): nothing to summarize honestly.
            raise Fail("gather", "transcript has zero segments")
        if stats["segPerMin"] < 0.5:
            # Quality gate (§7): phase-1 fallback transcription is the manual
            # runbook, so we proceed but flag the thin signal for the reviewer.
            log(f"WARN thin transcript ({stats['segPerMin']} seg/min < 0.5) — phase-1 proceeds; fallback is manual")

        attendance = load_attendance(code)
        tier1 = tier1_speakers(segments)
        invited_names = [
            i.get("name") or i.get("email")
            for i in ((meta.get("audience") or {}).get("invited") or [])
        ]
        # Tier 2 = participants − tier1 − bot (names already bot-filtered site-side).
        tier2 = None
        if attendance is not None:
            t1l = {n.lower() for n in tier1}
            tier2 = [
                p for p in attendance if (p.get("displayName", "").lower() not in t1l)
            ]

        summary = summarize(meta, segments)
        summary = validate(summary, segments, tier1, tier2, invited_names)

        page_title, markdown = compose(
            meta, summary, tier1, tier2, attendance is not None, had_generic_speaker(segments), stats
        )

        with open(out_path, "w") as f:
            f.write(markdown)
        log(f"composed page ({len(markdown)} bytes) -> {out_path}  variant={'minutes' if summary['motions'] else 'notes'}")

        if os.environ.get("SCRIBE_NOTES_PUBLISH", "false").lower() == "true":
            page_url = publish(page_title, markdown)
            log(f"published: {page_url}")
            notify("published", page_title, page_url=page_url)
        else:
            log("publish gated off (SCRIBE_NOTES_PUBLISH!=true) — composed to file only, no wiki write, no notify")
        print(out_path)  # stdout: the composed-page path, for the caller
    except Fail as f:
        log(f"FAIL at {f.stage}: {f}")
        try:
            notify("failed", title, stage=f.stage)
        except Exception as e:
            log(f"failure-notify errored: {e}")
        sys.exit(1)
    except Exception as e:  # unexpected
        log(f"UNEXPECTED: {e}")
        try:
            notify("failed", title, stage="worker")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
