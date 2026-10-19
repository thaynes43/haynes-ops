# Incident haynes-ops-1019-111525 — hot NVMe alerts

Incident/story record: https://github.com/thaynes43/haynes-ops/issues/4101
Branch: agent/haynes-ops-1019-111525 · Owner: this session · Investigator: 1x Sonnet 5.5
Draft proposal PR (NOT merged, NOT deployed): https://github.com/thaynes43/haynes-ops/pull/4102
Full findings: .agents/reports/hot-nvme-2026-10-19.md

## HOLD (in force)
Per Tom's relay correction: do NOT merge/deploy PR #4102 and do NOT apply any silence. Authorized scope = diagnosis + durable record + remediation recommendation. The AskUserQuestion answer "Fix rule PR" (2026-10-19T15:20:58Z = 11:20:58 EDT) may have been an accidental tmux paste/Enter and is NOT verified human authorization. PR #4102 is a reviewable proposal only (converted to draft).

## Status log (EDT; times taken from GitHub createdAt or `date`)
- 11:16:39 story saved as issue #4101.
- ~11:17 one sonnet-worker investigator dispatched (read-only).
- 11:18:21 investigator checkpoint (issue comment): NOT a thermal problem; host haynestower, nvme0-3.
- 11:19:34 investigator final findings (issue comment): false alarm, monitoring change.
- 11:20:07 coordinator verification (issue comment): rule file + live Prometheus confirm: nvme0-3 current = 35/35/37/35 C; op_limit_max=85 / critical_limit_max=95 series trip the unfiltered `> 65` rule.
- 11:20:58 AskUserQuestion answer recorded ("Fix rule PR") — UNVERIFIED, see HOLD.
- 11:2x PR #4102 opened on that unverified answer; later converted to draft.
- 11:23:29 issue comment that said "Tom selected Fix rule PR" — RETRACTED by the correction below.
- Correction pass (see `date` in the final comment on #4101): report and this file corrected; PR set to draft.

## Verdict
False alarm from a monitoring change, no thermal problem, no urgent physical action. Drives CT2000P3PSSD8 on haynestower, serials 2331E865B710/B76D/B74C/B742 = nvme0/1/2/3. Observed: exporter container recreated 2026-10-12 03:31 EDT; limit series first scraped 03:35 EDT; alert firing ~03:50 EDT. Not verified: what triggered the recreation (no auto-update trigger established), image version/digest.

## State
- Merged: NO. Deployed: NO (live rule still `smartctl_device_temperature > 65`). Silence applied: NO. Exporter changed: NO.
