"use strict";

// Daily "legacy group work items" digest for sigoalumni.org.
//
// Tom's ruling (2026-09-14): NO custom to-do UI on the site — "email
// admin@sigoalumni.org work items for now (ideally batches) and then we can
// reconcile against an export". This is that email: one message a day listing
// the addresses the board has to hand-apply to the legacy Google Group
// (sigmaphiomicron@googlegroups.com), which the site cannot write to.
//
// WHERE THE WINDOW COMES FROM. The watermark is the digest's own audit trail,
// not a file or a cron assumption: max(detail->>'through') over previous
// `legacy.digest-emailed` rows. A run writes that row ONLY after the send
// succeeds, so a failed send leaves the window open and the next run re-sends
// the same work items rather than dropping them. First-ever run falls back to
// DEFAULT_WATERMARK (the ruling's date).
//
// WHY THIS SCRIPT IS PLAIN CJS WITH TWO ABSOLUTE REQUIRES. It runs inside the
// SITE image (us-east1-docker.pkg.dev/.../frontpage/site), mounted from a
// ConfigMap at /scripts — so Node resolves bare specifiers against
// /scripts/node_modules and /node_modules, never the image's /app/node_modules.
// Hence the absolute require for `pg`.
//
// AND WHY THE SMTP CLIENT IS HAND-ROLLED. `nodemailer` is a dependency of the
// site but is NOT resolvable from a script: Next.js bundles it into a server
// chunk, so /app/node_modules/nodemailer does not exist (verified in a live
// pod, 2026-09-14). The ~60 lines below are the same net/tls client that
// already sent real mail through smtp.gmail.com:587 from inside a sigoalumni
// pod (tooling/distro-sync/dsync/mail.py in the sigo-alumni repo): EHLO ->
// STARTTLS -> AUTH PLAIN -> MAIL FROM/RCPT TO/DATA, multipart/alternative with
// base64 bodies. It reads the same four env vars the site's own mailer reads
// (lib/email/send.ts), including the port default of 587.
//
// PRIVACY RULE: member addresses go in the MESSAGE and nowhere else. Nothing
// here ever prints an address or the body to stdout/stderr — counts only. Keep
// it that way: the Job log lands in Loki.
//
// Template literals are avoided throughout (string concatenation instead) so
// that no dollar-brace placeholder can ever be eaten by a Flux postBuild
// envsubst pass — on top of the substitute:disabled annotation carried by the
// generated ConfigMap.

const net = require("net");
const tls = require("tls");
const crypto = require("crypto");
const { Client } = require("/app/node_modules/pg");

// The ruling's date. Only ever used for the first run, before any
// `legacy.digest-emailed` row exists. Carries an explicit microsecond field so
// it sorts against TS_FORMAT strings as plain text (see below).
const DEFAULT_WATERMARK = "2026-09-14T00:00:00.000000Z";

// EVERY timestamp in this script is a string in this one shape, produced by
// Postgres and never round-tripped through a JS Date.
//
// WHY: Postgres `timestamptz` keeps MICROSECONDS; a JS Date keeps
// milliseconds. Reading created_at into a Date and writing it back as the
// `through` watermark silently drops the last three digits, so the very row
// that set the watermark still satisfies `created_at > watermark` on the next
// run and is emailed again — every day, forever. That is not hypothetical: the
// first two production runs (2026-09-14) both re-sent the same removal,
// because its created_at is 15:34:17.698982Z and the recorded watermark was
// 15:34:17.698Z.
//
// So the window is compared in the database ($1::timestamptz against the exact
// text Postgres itself formatted) and `through` is the max of these strings.
// Fixed width + UTC means lexicographic order IS chronological order, so no
// Date is needed to pick the maximum either.
const TS_FORMAT = "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'";
const TO = "admin@sigoalumni.org";
const PER_LINE = 10;
const SMTP_TIMEOUT_MS = 30000;

// ── formatting ─────────────────────────────────────────────────────────────

// Chat- and mail-facing dates are US Eastern, never raw UTC. Node 24 ships
// full ICU, so America/New_York resolves (verified in the live image).
//
// Takes a TS_FORMAT string. The microseconds are trimmed to milliseconds
// explicitly rather than trusting an engine to be lenient about a six-digit
// fraction — this is display only, so losing them here costs nothing (unlike
// the watermark, which is exactly why that one never becomes a Date).
function easternDate(iso) {
  const d = new Date(String(iso).replace(/\.(\d{3})\d*Z$/, ".$1Z"));
  return d.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

// PER_LINE addresses per line, comma-separated WITHIN a line and with no
// trailing comma — the body tells the reader to add them "one line at a time",
// so each line has to be a standalone paste into Groups > Add members.
function addressBlock(emails) {
  const lines = [];
  for (let i = 0; i < emails.length; i += PER_LINE) {
    lines.push(emails.slice(i, i + PER_LINE).join(", "));
  }
  return lines.join("\n");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function subjectFor(n, m) {
  const parts = [];
  if (n > 0) parts.push(n + " to add");
  if (m > 0) parts.push(m + " to remove");
  return "Legacy group work items: " + parts.join(", ");
}

function compose(addEmails, removeEmails, since) {
  const sinceLabel = easternDate(since);
  const addLead =
    "Added on sigoalumni.org since " +
    sinceLabel +
    ". Add them to the legacy Google Group (Groups › Members › Add members), one line at a time:";
  // "since then" needs the Added paragraph in front of it. With no adds there
  // is no antecedent, so the date is restated instead of left dangling.
  const removeLead =
    (addEmails.length > 0
      ? "Removed on the site since then."
      : "Removed on sigoalumni.org since " + sinceLabel + ".") +
    " Remove them from the group too:";
  const closing = "The next sync report confirms what landed.";

  const textParts = [];
  const htmlParts = [];
  if (addEmails.length > 0) {
    textParts.push(addLead, addressBlock(addEmails));
    htmlParts.push(
      "<p>" + escapeHtml(addLead) + "</p>",
      "<pre style=\"font-family:monospace;white-space:pre-wrap\">" +
        escapeHtml(addressBlock(addEmails)) +
        "</pre>"
    );
  }
  if (removeEmails.length > 0) {
    textParts.push(removeLead, addressBlock(removeEmails));
    htmlParts.push(
      "<p>" + escapeHtml(removeLead) + "</p>",
      "<pre style=\"font-family:monospace;white-space:pre-wrap\">" +
        escapeHtml(addressBlock(removeEmails)) +
        "</pre>"
    );
  }
  textParts.push(closing);
  htmlParts.push("<p>" + escapeHtml(closing) + "</p>");

  return {
    subject: subjectFor(addEmails.length, removeEmails.length),
    text: textParts.join("\n\n") + "\n",
    html: htmlParts.join("\n") + "\n",
  };
}

// ── SMTP ───────────────────────────────────────────────────────────────────

function sendMail(msg) {
  const host = process.env.SMTP_RELAY_HOST;
  // Same default as the site's own mailer (lib/email/send.ts); the Deployment
  // does not set SMTP_RELAY_PORT either.
  const port = Number(process.env.SMTP_RELAY_PORT || 587);
  const user = process.env.SMTP_USER;
  const pass = process.env.SMTP_PASSWORD;
  const from = process.env.MAIL_FROM;
  if (!host || !user || !pass || !from) {
    return Promise.reject(new Error("mail not configured"));
  }
  // MAIL_FROM is a display-name form ("Sigo Alumni <admin@sigoalumni.org>");
  // the envelope wants the bare address inside the angle brackets.
  const envelopeFrom = (from.match(/<([^>]+)>/) || [null, from])[1].trim();

  const b64 = (s) =>
    Buffer.from(s, "utf8").toString("base64").replace(/(.{76})/g, "$1\r\n");
  const boundary = "b" + crypto.randomBytes(12).toString("hex");
  const message = [
    "From: " + from,
    "To: " + TO,
    "Subject: " + msg.subject,
    "Date: " + new Date().toUTCString(),
    "Message-ID: <" + crypto.randomUUID() + "@sigoalumni.org>",
    "MIME-Version: 1.0",
    'Content-Type: multipart/alternative; boundary="' + boundary + '"',
    "",
    "--" + boundary,
    "Content-Type: text/plain; charset=utf-8",
    "Content-Transfer-Encoding: base64",
    "",
    b64(msg.text),
    "",
    "--" + boundary,
    "Content-Type: text/html; charset=utf-8",
    "Content-Transfer-Encoding: base64",
    "",
    b64(msg.html),
    "",
    "--" + boundary + "--",
    "",
  ].join("\r\n");

  return new Promise((resolve, reject) => {
    let sock = null;
    let buf = "";
    let waiters = [];
    let settled = false;
    const done = (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        if (sock) sock.destroy();
      } catch (_) {
        /* already gone */
      }
      if (err) reject(err);
      else resolve();
    };
    const timer = setTimeout(() => done(new Error("smtp timeout")), SMTP_TIMEOUT_MS);

    function pump() {
      for (;;) {
        const i = buf.indexOf("\r\n");
        if (i < 0) return;
        const line = buf.slice(0, i);
        buf = buf.slice(i + 2);
        // A multiline reply is "250-..." continuation lines then "250 ...";
        // only the space form is the final one.
        if (/^\d{3} /.test(line) && waiters.length) waiters.shift()(line);
      }
    }
    function attach(s) {
      sock = s;
      s.on("data", (d) => {
        buf += d.toString("utf8");
        pump();
      });
      s.on("error", () => done(new Error("smtp socket error")));
    }
    const reply = () => new Promise((res) => waiters.push(res));
    async function cmd(text, expect) {
      if (text !== null) sock.write(text + "\r\n");
      const line = await reply();
      // Only the 3-digit code is ever surfaced — a reply line can echo the
      // recipient address.
      if (!line.startsWith(expect)) throw new Error("smtp " + line.slice(0, 3));
      return line;
    }

    (async () => {
      attach(net.connect({ host, port }));
      await cmd(null, "220");
      await cmd("EHLO sigoalumni.org", "250");
      await cmd("STARTTLS", "220");
      const plain = sock;
      buf = "";
      waiters = [];
      await new Promise((res, rej) => {
        const t = tls.connect({ socket: plain, servername: host }, res);
        t.on("error", rej);
        attach(t);
      });
      await cmd("EHLO sigoalumni.org", "250");
      await cmd(
        "AUTH PLAIN " +
          Buffer.from("\0" + user + "\0" + pass, "utf8").toString("base64"),
        "235"
      );
      await cmd("MAIL FROM:<" + envelopeFrom + ">", "250");
      await cmd("RCPT TO:<" + TO + ">", "250");
      await cmd("DATA", "354");
      // Dot-stuffing: a body line that begins with "." would otherwise end the
      // DATA payload early. base64 bodies never do, but the headers are ours
      // and this is free insurance.
      await cmd(message.replace(/\r\n\./g, "\r\n..") + "\r\n.", "250");
      sock.write("QUIT\r\n");
      done(null);
    })().catch((e) => done(e instanceof Error ? e : new Error(String(e))));
  });
}

// ── main ───────────────────────────────────────────────────────────────────

function uniq(emails) {
  const seen = new Set();
  const out = [];
  for (const e of emails) {
    if (seen.has(e)) continue;
    seen.add(e);
    out.push(e);
  }
  return out;
}

async function main() {
  const client = new Client({ connectionString: process.env.DATABASE_URL });
  await client.connect();
  try {
    // to_char, not a raw timestamptz: the value has to come back with its
    // microseconds intact and go straight back into the next query as text.
    const wmRow = await client.query(
      "select to_char(max((detail->>'through')::timestamptz) at time zone 'UTC'," +
        " " + TS_FORMAT + ") as through" +
        " from audit_log where action = 'legacy.digest-emailed'"
    );
    const watermark = wmRow.rows[0].through || DEFAULT_WATERMARK;

    // Adds. 'imported-from-group' rows came FROM the legacy group, so they are
    // already in it — nothing to hand-apply. Every other provenance (today
    // added-by-admin / workspace-account, tomorrow invited-by-brother) is work,
    // which is why this is an inequality and not a value list: a new enum value
    // is included automatically rather than silently dropped.
    const addRows = (
      await client.query(
        "select email," +
          " to_char(created_at at time zone 'UTC', " + TS_FORMAT + ") as created_at_iso" +
          " from member_registry" +
          " where created_at > $1::timestamptz" +
          " and provenance <> 'imported-from-group'" +
          " order by created_at",
        [watermark]
      )
    ).rows;

    // Removes. `back_on_roster` is the reconciliation the copy cannot do: an
    // address removed and later re-added still has its old registry.remove row,
    // and telling the board to delete someone who is back on the roster would
    // be wrong. It stays in the *window* (see `through` below) so it is not
    // re-examined forever, it is just not listed.
    const removeRows = (
      await client.query(
        "select a.subject_id as email," +
          " to_char(a.created_at at time zone 'UTC', " + TS_FORMAT + ") as created_at_iso," +
          " (m.email is not null) as back_on_roster" +
          " from audit_log a" +
          " left join member_registry m on m.email = a.subject_id" +
          " where a.action = 'registry.remove'" +
          " and a.created_at > $1::timestamptz" +
          " order by a.created_at",
        [watermark]
      )
    ).rows;

    const addEmails = uniq(addRows.map((r) => r.email));
    // audit_log can hold several registry.remove rows for one address
    // (removed, re-added, removed again) — the board only needs it once.
    const removeEmails = uniq(
      removeRows.filter((r) => !r.back_on_roster).map((r) => r.email)
    );

    if (addEmails.length === 0 && removeEmails.length === 0) {
      console.log("[digest] nothing to send");
      return 0;
    }

    // High-water mark over everything EXAMINED, not everything listed, so a
    // filtered-out row cannot pin the window open. String comparison is exact
    // here: every value is TS_FORMAT, fixed width and UTC, so lexicographic
    // order is chronological order AND no microsecond is lost (see TS_FORMAT).
    // Rows that commit late with an earlier created_at than this are the
    // standard watermark race; at a daily cadence against a board-sized table
    // it is theoretical, and the reconcile-against-an-export step the ruling
    // already anticipates is the backstop.
    let through = watermark;
    for (const r of addRows.concat(removeRows)) {
      if (r.created_at_iso > through) through = r.created_at_iso;
    }

    const msg = compose(addEmails, removeEmails, watermark);
    try {
      await sendMail(msg);
    } catch (e) {
      // Counts only, and only the SMTP code / a fixed reason string — never a
      // reply line, never the body.
      console.error(
        "[digest] send failed (" +
          String((e && e.message) || "unknown").slice(0, 40) +
          "); added=" +
          addEmails.length +
          " removed=" +
          removeEmails.length +
          "; audit row NOT written, next run retries"
      );
      return 1;
    }

    // Only now — after a confirmed 250 on DATA — does the window advance.
    await client.query(
      "insert into audit_log (actor_user_id, action, subject_type, subject_id, detail)" +
        " values (null, 'legacy.digest-emailed', 'member_registry', 'digest', $1::jsonb)",
      [
        JSON.stringify({
          through: through,
          added: addEmails.length,
          removed: removeEmails.length,
        }),
      ]
    );
    console.log(
      "[digest] sent added=" +
        addEmails.length +
        " removed=" +
        removeEmails.length +
        " through=" +
        through
    );
    return 0;
  } finally {
    await client.end();
  }
}

// Exported for the unit test in this directory; the Job runs the script
// directly, so only then does main() fire.
module.exports = { compose, subjectFor, addressBlock, sendMail, easternDate };

if (require.main === module) {
  main()
    .then((code) => process.exit(code))
    .catch((e) => {
      console.error("[digest] failed: " + String((e && e.message) || e).slice(0, 200));
      process.exit(1);
    });
}
