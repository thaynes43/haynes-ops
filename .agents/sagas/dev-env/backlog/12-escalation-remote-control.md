# 12 — failure escalation → interactive remote-control session (dev-env)

**Status:** proposed design — feasibility CONFIRMED 2026-07-28 (claude-code 2.1.217
in the dev-env pod has `--remote-control [name]`; tmux supplies the TTY). Awaiting
Tom's call on the notification path + a dev-env bounce window for the watcher.
**Depends on:** 06 (agent-run), 07 (Option A — decides what does NOT get dispatched)

## The wish (Tom, 2026-07-28)

> "if the initial agent responding to alerts or sheparding fails it would be good to
> spawn a remote control agent who I could interact with, Fable if possible."

Today a failed/held run ends in a Pushover page with a one-line verdict
(`BREAK-GLASS: …` / `HOLD: …`) and the trail stops there. The wish: that page should
come with a **live, phone-drivable session** already loaded with the failure context,
so Tom can interrogate/steer from bed instead of opening a laptop.

## Feasibility — confirmed

- `claude --remote-control <name> "<initial prompt>"` starts an interactive session
  registered for Remote Control (drivable from claude.ai / the mobile app).
  Present in the dev-env pod's claude 2.1.217 (`--remote-control`,
  `--remote-control-session-name-prefix`).
- It needs a TTY → run it detached under the pod's existing tmux:
  `tmux new-window -t main -n esc-<key> "claude --remote-control 'esc-<key>' --model fable '<context>'"`.
- **Fable**: available on the Max plan in this pod (alias `fable`). Tom's
  quota concern (07-28) was about *scheduled/automated* consumers maxing the pool —
  an escalation session is rare (a few per month) and human-attended, which is
  exactly where Fable belongs. Fall back to `opus` if `fable` is ever unavailable.

## Design — signal crosses, work does not

The saga-07 boundary stays intact: the contained agents (shepherd / responder /
gate) still run their own LLM turns. Only **failure metadata** (a short reason
string) crosses into dev-env, and the session it spawns has the human in the loop
from message one. This is NOT dispatch-into-dev-env of hostile-input work.

```
shepherd / responder / gate            dev-env pod (24/7, tmux)
  on terminal failure:                   escalation-watch loop (60s poll):
  append entry to CM ──────────────────▶ kubectl get cm upgrade-escalations
  upgrade-agent/upgrade-escalations      diff vs PVC-local seen-file
  {source, reason, summary≤400ch,        spawn tmux window:
   ts, run-ref}                            claude --remote-control esc-<key>
                                             --model fable "<context prompt>"
  gate page gains one line:              (session appears in claude.ai /
  "| escalation session: esc-<key>"       mobile app for Tom)
```

- **Writers** (all already hold namespace-scoped CM write — spend/state/orphan CMs
  prove it): shepherd on `BREAK-GLASS:`/`HOLD:`/rc≠0, triage on a failed remediate,
  responder on rc≠0 / fallback-blocked, gate optionally on repeat-page.
- **Watcher** (dev-env): tiny `escalation-watch.sh` in the GitOps-managed dev-env
  config, launched as a persistent tmux window at pod start. The dev-env SA is
  READ-ONLY — it cannot ack entries in the CM, so dedupe state lives on the PVC
  (`~/.local/state/escalations-seen`); writers TTL-prune their own entries (the
  responder state-CM pattern).
- **Notification**: the health gate already reads coordination state on its 30-min
  cadence — it appends `escalation session: esc-<key>` to the page it was sending
  anyway. (Alternative: give the watcher Pushover creds for an instant page —
  Tom's call; the gate route adds zero new credentials.)
- **Prompt-injection note**: the summary field originates partly from LLM output
  that read hostile input (release notes, alert annotations). The watcher truncates
  it (≤400 chars), strips control characters, and the spawned session's initial
  prompt frames it as *data to verify against the run logs*, never as instructions.
  The human is present for every action the session takes.

## Split (bounce-aware)

1. **PR A — this doc.** (You're reading it.)
2. **PR B — writers**: `escalate()` helper in the shared coordination-lib +
   call-sites in run-shepherd.sh / triage.sh / respond.sh / gate page body + RBAC
   for the new CM name. Touches `upgrade-agent` only — merges without a dev-env
   bounce.
3. **PR C — watcher**: dev-env config (script + tmux autostart). **Held-draft** like
   every dev-env change — merging bounces this pod; land it at the same deliberate
   bounce window as the pending dev-env image PR (#2251).

## Acceptance

- Kill a shepherd run on purpose (bogus ramp component → fail-closed) → within one
  gate cycle the page carries `escalation session: esc-…`, and the session is
  drivable from the phone with the failure context pre-loaded, on Fable.
- A quiet week spawns zero sessions and the watcher costs $0 (no LLM in the loop —
  pure kubectl poll + tmux).
