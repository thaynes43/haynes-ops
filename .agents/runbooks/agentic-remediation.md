# Agentic remediation — the `rem-*` lane

**What it is.** A critical alert whose read-only diagnosis says `ACTION: urgent`
and which is still firing is *handed to a machine*, not paged: the
alert-responder files a `rem-responder-<sig8>` order, the `dev-env-ops` executor
runs a headless Claude Code session (`OPS_REM_MODEL`, Opus 5.5 since 2026-09-23,
`xhigh`, 40 min / 120 turns, no Remote Control, nobody paged) that triages, fixes inside its containment,
verifies against the live condition, and closes silently into the quiet digest
(Pushover priority -1). Tom is paged only when the lane cannot fix or tried and
failed — and then with a joinable `esc-rem-<sig8>` session, not a summary.
North star (Tom, 2026-08-23): **page only when there is a problem he must solve.**

**Wiring** (all GitOps-managed under `kubernetes/main/apps/upgrade-agent/`):

| step | where |
|---|---|
| diagnosis → `ACTION: urgent` + still firing → handoff (fails open to a page) | `alert-responder/app/resources/respond.sh` |
| order filed in ConfigMap `upgrade-work-orders` (`rem-<source>-<sig8>`, `class: remediation`) | `health-gate/app/resources/remediate.sh` |
| watcher claims it (single-flight per lane, spawn order esc → wo → rem) and spawns the session | `dev-env-ops/app/resources/work-order-watch.sh` → `session-launch.sh` |
| the session's contract | `dev-env-ops/app/resources/ops-claude.md` → *The autonomous remediation contract* |
| close-out vocabulary (`done` silent · `working` heartbeat · `escalate` → esc-* + page · `failed` ⇒ escalate) | `dev-env-ops/app/resources/order-status.sh` |
| "is dev work causing this?" evidence | `dev-env-ops/app/resources/dev-activity-check.sh` (reads `declare-activity` records from the dev-env pod) |
| stale-lane watchdog, `rem-*` (unclaimed 20 min / claimed 120 min → escalate) | `respond.sh` `rem_watchdog` |
| stale-lane watchdog, `wo-*` (claimed, no `working` heartbeat for `OPS_WO_STALE_MINUTES` = 180 min → `failed`: frees the lane, pages once, window kept for the post-mortem). `esc-*` is exempt — idling on a human is its normal state | `dev-env-ops/app/resources/work-order-watch.sh` `wo_watchdog` |

## Decision table

| case | the session found | close-out |
|---|---|---|
| 1 | fixed, and the live condition re-queried clean | `done` — silent, digest line |
| 2 | nothing to fix: already self-healed, a [known-noise](known-noise-and-non-remediation.md) match, or dev-caused and harmless (a MATCHED `dev-activity-check` declaration **and** nothing actually broken) | `done` — name the entry / declaration in the note |
| 3 | needs something the lane cannot do: outside RBAC or egress, physical, a decision (bump vs hold) | `escalate` with the exact human steps |
| 4 | tried, and it did not hold | `escalate` with what was tried and the current state |

A declaration or a noise match can turn a case 4 into a case 2; it can never
suppress a case 3. A fix that cannot be verified is a failed fix — escalate it.

## Per-alert playbooks

Alert-specific procedures live beside this file in `.agents/runbooks/` — the one
directory the responder's shallow clone actually searches:

- [`ceph-daemon-crash.md`](ceph-daemon-crash.md) — `CephDaemonCrash`: verify the
  daemon is back and PGs are clean, archive the inspected crash ids, escalate
  on a repeat signature or a daemon that stays down.
- [`cpu-starvation.md`](cpu-starvation.md): `NodeLoadSaturated`. The lane
  diagnoses and names the CPU consumer. It never deletes or restarts the dev-env
  pod, and never deletes the broker PVC for a starvation restart loop.
- [`mqtt-broker.md`](mqtt-broker.md): `MosquittoNotReady`, the MQTT broker that
  every Zigbee light depends on. Never delete `data-mosquitto-0`.
- [`known-noise-and-non-remediation.md`](known-noise-and-non-remediation.md) —
  the cases where the obvious fix is wrong.

## Known gaps (2026-10-05 audit)

- **The esc-* "join from the phone" handle did not work from 2026-09-03 to
  2026-10-06.** No `wo-*`/`esc-*` session registered with Remote Control: the
  pod's credentials file held the setup token, which lacks the
  `user:sessions:claude_code` scope. Fixed by
  [haynes-ops#3414](https://github.com/thaynes43/haynes-ops/issues/3414)'s ruling:
  dev-env-ops has its own monthly Max login, and every page checks the
  registration before it offers a link. See
  [dev-env-ops Max login](#dev-env-ops-max-login-remote-control-for-wo-esc-).
- **Merging anything mounted in `dev-env-ops` restarts it** (its scripts, the
  `upgrade-coordination-lib` ConfigMap from `health-gate/`, its Secrets) and ends
  every session in it. Merge those when no order is `claimed`
  ([haynes-ops#3415](https://github.com/thaynes43/haynes-ops/issues/3415)).
- The esc-* model is the HelmRelease's `OPS_ESC_MODEL`. Writers must not put a
  `model` in an order unless they mean to override it.
- **One setup token carries every automated path**: the shepherd, triage, the
  responder, all three dev-env-ops lanes and vexa scribe-notes use 1Password
  `claude-code` / `CLAUDE_CODE_OAUTH_TOKEN` (a `claude setup-token`, first
  wired 2026-07-13 per dev-env saga backlog 07, unchanged on the dev-env-ops PVC
  since 2026-08-20; setup tokens last about a year). Nothing records its mint
  date or warns before it expires. When it dies, the shepherd and responder quietly fall
  back to the metered key (capped at $50 + $15 a month), and the only alarm is
  dev-env-ops' daily setup-token probe (page "[dev-env-ops] claude SETUP TOKEN
  rejected", since #3414; before that the probe's page named the wrong file).
  Re-mint it before about 2027-07 (ceremony: dev-env saga backlog 04-auth.md).
  It is a different credential from the monthly Max login below.

## dev-env-ops Max login (Remote Control for `wo-*`/`esc-*`)

Tom's ruling on [haynes-ops#3414](https://github.com/thaynes43/haynes-ops/issues/3414)
(2026-10-06): `dev-env-ops` has its **own** Claude Max login, so the sessions he
may join are really on his phone. He renews it monthly through the same relay
ceremony as the dev-env pod's login: an agent sends him the OAuth link and he
sends back the code.

**Where it lives.** `/home/dev/.claude/.credentials.json` in container `app` of
Deployment `dev-env-ops` (namespace `upgrade-agent`; `CLAUDE_CONFIG_DIR=/home/dev/.claude`),
on PVC `dev-env-ops-home`. It survives pod restarts. Only `claude auth login`
writes it (and the CLI, when it refreshes the token). `ops-init.sh` never writes
it. Until #3414, ops-init made this file from the setup token and rewrote it on
every boot. Now it deletes that old synthesized file once and leaves real logins
alone.

**Which sessions use it.**

| lane | credential | Remote Control |
|---|---|---|
| `esc-*`, `wo-*` (shepherd orders) | the Max login (`env -u CLAUDE_CODE_OAUTH_TOKEN`) | yes, `--remote-control <key>` |
| `wo-*` with `class: curation` (daily cigar batch) | setup token | no. It is an unattended daily batch, and registering it would add an entry to Tom's list every day |
| `rem-*` (headless) | setup token | no |
| any of the first row when the Max login is missing, expired, or rejected | setup token | no. The page says so and why, and gives the attach command |

**Pages never offer a link that is not there.** `session-launch.sh` waits up to
120 s for the CLI's registry record for the session's tmux pane
(`~/.claude/sessions/<pid>.json`) to show a `bridgeSessionId`
(`rc-state.sh`). Only then does the `esc-*` page include
`https://claude.ai/code/<bridgeSessionId>`. Otherwise the page says Remote Control
is not available, gives the reason, and gives
`kubectl -n upgrade-agent exec -it deploy/dev-env-ops -c app -- tmux attach -t 'ops:<key>'`.
The `failed` page from `order-status.sh` checks again the same way. Each
session's verdict is in Loki: `{namespace="upgrade-agent", pod=~"dev-env-ops.*"} |= "event=rc"`.

**Expiry and health pages** (`work-order-watch.sh` step 5, at boot and then
daily). The titles say this pod, so they cannot be confused with the dev-env
pod's own "dev-env: Claude Max login expiring" page from auth-watch:

- `[dev-env-ops] Claude Max login expiring (this pod's own login, not dev-env's)`:
  7 days or fewer left, or expired. Run the ceremony below.
- `[dev-env-ops] Claude Max login REJECTED (this pod's own login, not dev-env's)`:
  days are left but the API refused it (revoked). Run the ceremony below.
- `[dev-env-ops] claude SETUP TOKEN rejected`: the other credential (see Known
  gaps). Its fix is the setup-token re-mint, not this ceremony.
- No login at all is logged (`what=no-max-login`) and not paged: every `esc-*`
  page already says Remote Control is unavailable.

Check it by hand with
`kubectl -n upgrade-agent exec deploy/dev-env-ops -c app -- bash /opt/dev-env-ops/login-check.sh`
(exit 0 fine, 1 at 7 days or fewer or expired, 2 no login). The output is dates
and day counts only.

**One owner per token family.** Each `claude auth login` is a separate grant
with its own rotating refresh token. **Never copy a `.credentials.json` from the
dev-env pod (or anywhere else) into this pod, and never the reverse.** A copy
gives the same refresh token two owners in two pods. The CLI's refresh lock only
works between processes that share one file in one pid namespace, so the first
rotation in one pod leaves the other replaying a used refresh token, and the
server revokes the whole family: both pods are logged out mid-task. That is the
failure class behind the 2026-08-29 incident and DESIGN-001 D-11 in
thaynes43/dev-env. The record (dev-env saga backlog `04-auth.md`, #2588) says
the dev-env pod's login "expired mid-task" at about 09:04 that day, with the
likely cause that 5 or more sessions sharing one file raced the rotation. The
cause was never proven. Within one pod the CLI's lock serializes refreshes, and
dev-env has run up to 14 Remote Control sessions on one file since then. In this
pod, at most one `wo-*` and one `esc-*` session (each lane is single-flight)
plus the probes share the file.

### Renewal ceremony (run from a dev-env session; Tom spends about a minute on his phone)

The agent drives everything and Tom does two things: he opens the link and he
sends back the code. **The OAuth URL, the code and anything inside
`.credentials.json` are secrets for the life of the flow.** They go in the chat
reply to Tom and the tmux pane only, never into git, a PR, an issue, a memory
file, a log or a handoff note. The code goes into the pod through kubectl's
**stdin**, not as a command argument, because exec arguments travel in the API
request URL.

```bash
POD=$(kubectl -n upgrade-agent get pod -l app.kubernetes.io/name=dev-env-ops -o jsonpath='{.items[0].metadata.name}')
x() { kubectl -n upgrade-agent exec "$POD" -c app -- "$@"; }

# 0. How much is left (dates only, safe to quote).
x bash /opt/dev-env-ops/login-check.sh

# 1. Start the login in its own wide tmux session in the pod. Use session `login`,
#    never `ops`: the watcher owns `ops`. Strip both env credentials.
x tmux kill-session -t login 2>/dev/null
x tmux new-session -d -s login -x 300 -y 50 -c /home/dev \
  "env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY claude auth login; echo LOGIN_EXIT=\$?; sleep 3600"
x timeout 60 bash -c 'until tmux capture-pane -p -J -t login | grep -q "https://"; do sleep 2; done'
x bash -c "tmux capture-pane -p -J -t login | grep -o 'https://claude.com/cai/oauth/authorize?[^ ]*'"
```

2. Put that URL in the reply to Tom, **bare, on its own line** (no backticks, no
   markdown link), and say it is for the **dev-env-ops** login. He opens it,
   signs in, and pastes back the code the page shows (`<code>#<state>`).
3. Send the code in through stdin, literally, then wait for the result:

```bash
printf '%s' '<the code he pasted>' | kubectl -n upgrade-agent exec -i "$POD" -c app -- \
  bash -c 'IFS= read -r c; tmux send-keys -t login -l "$c"; tmux send-keys -t login Enter'
x timeout 60 bash -c 'until tmux capture-pane -p -J -t login | grep -q LOGIN_EXIT; do sleep 2; done'
x bash -c "tmux capture-pane -p -J -t login | grep -E 'Login successful|LOGIN_EXIT|rror'"
```

   `Login successful.` with `LOGIN_EXIT=0` means it worked. For anything else,
   kill the `login` session and start again at step 1 with a fresh URL. Codes
   are single-use and must match the URL's `state`.
4. Verify, prove Remote Control, then clean up:

```bash
x bash /opt/dev-env-ops/login-check.sh          # expect about 30 days left, exit 0
x bash -c 'env -u CLAUDE_CODE_OAUTH_TOKEN -u ANTHROPIC_API_KEY claude auth status | jq -c "{loggedIn, authMethod, subscriptionType}"'
                                                 # true, "claude.ai", "max". Never print the whole status: it has the email and org id
x bash /opt/dev-env-ops/rc-selftest.sh           # SELFTEST: REGISTERED
x tmux kill-session -t login
```

`rc-selftest.sh` starts `claude --remote-control wo-rc-selftest-<stamp>` with no
prompt in its own tmux session (`rcselftest`, never `ops`). It waits for the
`bridgeSessionId`, prints the verdict without the link, and ends the session. It
files no order, sends no page and touches no live session. Its one entry on Tom's
list goes offline when it ends, and because it was never used, the CLI archives
it at the next Remote Control start from this home. For a full end-to-end check
through the watcher, file a synthetic no-op `wo-*` order instead (`wo-*` is
quiet on success, see the dev-env-ops executor notes). That leaves a used entry
on Tom's list until he archives it.

Nothing needs a restart afterwards. Every new `wo-*`/`esc-*` launch reads
`login-check.sh` again. A session already running on the setup token stays
without Remote Control until it ends.

## What has actually been exercised

- 2026-08-23: a synthetic `rem-*` order end to end (#2569 wired the executor;
  #2572 added the missing responder call site — **test the deployed system,
  not the diff**). The session correctly called the synthetic order fake.
- 2026-09-11 `CephDaemonCrash`: the responder diagnosed it correctly but chose
  `investigate` (paged) because no runbook said the lane may act; the crash was
  archived by hand a day later. The `ceph-daemon-crash.md` playbook and the
  responder prompt clause that makes `urgent` the handoff verb for
  runbook-covered alerts date from that incident.
- 2026-09-05 to 2026-10-05 (audit, haynes-ops#3414): 12 `rem-*` orders, all
  closed by the session itself in 4 to 15 minutes. 9 `done`: real fixes on 3
  incidents (multus conf regenerated on talosw01, a Ceph crash verified and
  archived, dev-env PVC space freed twice) and 5 "nothing to fix" verdicts
  (self-healed, or a matched `declare-activity`). 3 `escalate`: two physical
  device faults (Z-Wave PoE coordinator, three Hue bulbs off mains) and the
  2026-09-12 drill. Zero metered API spend.
