# Story 181 — Home Assistant 2026.10.3 patch review

2026-10-23, bounded source-only review. Sole execution owner remains `/root`; no runtime, Home Assistant API, backup, credential, auth, policy, commit, push or worker action taken. Earlier exclusions and other owner's OIDC4246 scope remain untouched. Patch deployment is already authorized; this review establishes no new approval question or hold.

## Current source and grouped scope

Public `main` inspected directly: Core **2026.10.2**, same-pod code-server **4.141.0@sha256:72ab24b5680ee597dd647b30d82625752f40109b5f61bef90ffbf1ea1f02dbb8**, separate ha-mcp companion **8.7.0**. Local shared origin/main was `104f47ed3206cb5988ee77ba8f2aaa35d9f9c4b0` at review. Group rules intentionally include all three so a sidecar change shares the HA restart. No Core patch requirement to bump either companion was found. No matching `renovate/*home-assistant*` branch was visible in one public git read; public PR-list fetch was unavailable. Driver should inspect the actual grouped bot diff when created and include any real companion delta once, without duplicating prior delivery.

Sources: [current HA HelmRelease](https://github.com/thaynes43/haynes-ops/blob/main/kubernetes/main/apps/home-automation/home-assistant/app/helmrelease.yaml), [current ha-mcp](https://github.com/thaynes43/haynes-ops/blob/main/kubernetes/main/apps/home-automation/ha-mcp/app/helmrelease.yaml), `.renovate/groups.json5`.

## Release and configuration relevance

[Official 2026.10.3 release](https://github.com/home-assistant/core/releases/tag/2026.10.3), published October23 18:50Z. Exact [2026.10.2→2026.10.3 source diff](https://github.com/home-assistant/core/compare/2026.10.2...2026.10.3) contains50 files. It updates Python3.14.8, base2026.10.1 and frontend20260930.5, plus integration fixes. Potentially relevant fixes include Proxmox update detection, duplicate UniFi Access events, SMTP TLS-context isolation, Home Connect event recovery and KNX startup. Proxmox/UniFi platform presence does not prove those HA integrations are configured; no live config was inspected. SMTP still honors configured SSL verification; do not change auth/settings for this patch.

No local deployment/config adaptation identified. Preserve `/config` claim, `/media` mounts, Multus IoT/Sonos networks, security context and existing custom integrations. Python/base changes make clean custom-component startup worth checking. Core REST/websocket changes requiring ha-mcp adaptation were not found in this diff.

## Database migration finding

**No new Recorder schema migration is introduced by this patch.** No recorder files change in the exact50-file diff; Recorder const is byte-identical and target [db_schema.py](https://github.com/home-assistant/core/blob/2026.10.3/homeassistant/components/recorder/db_schema.py) has `SCHEMA_VERSION = 53`. Existing older/incomplete migrations could still run at startup, so this is not proof of live database state.

KNX changes startup handling around an **existing** telegram-store migration: it freezes the bootstrap stage timeout while initialization completes. It does not add a new telegram schema version. If KNX logs an existing migration in progress, avoid interrupting it with repeated restarts. [Exact KNX source](https://github.com/home-assistant/core/blob/2026.10.3/homeassistant/components/knx/telegrams.py).

## Exact backup and rollback checks for driver

1. Fresh pre-upgrade backup must finish successfully while Core is still2026.10.2. Record backup ID/time/source version, nonzero artifact size and usable storage destination; an accepted creation request or idle backup manager alone is not completion. Confirm the emergency encryption/recovery material is already accessible through the normal authorized owner process; do not print it. [Official backup state/actions](https://www.home-assistant.io/integrations/backup/).
2. Confirm backup scope includes `/config` configuration, `.storage`, custom integrations and the actual Recorder database recovery path. Source alone does not reveal the active DB engine: default SQLite is under `/config`, whereas an external DB needs matching existing backup coverage. Do not assume a HA config archive or PVC snapshot includes an external database. Preserve the successful backup until acceptance; don't delete/prune/repair history as an upgrade check.
3. Save exact pre-upgrade image/group pins and workload/PVC identities. For a regression, revert the Git image/group change and let Flux restore the old image; Helm's rollback strategy is not a data restore. With no new Recorder schema in this patch, an image rollback has no newly identified Recorder downgrade obstacle, but do not claim universal rollback compatibility for custom integrations, `.storage` or a partially completed migration.
4. Post-rollout, confirm actual Core2026.10.3 and the intended two-container HA pod, no repeated startup/recorder/custom-component errors, existing history and normal representative integrations. Check MQTT/Z-Wave and ordinary HA websocket/MCP read connectivity after HA reconnects, then compare unavailable-entity/error baselines. No destructive service calls, device resets or forced playback are needed. Preserve code-server/ha-mcp pins unless the actual group diff updates them.

[Recorder documentation](https://www.home-assistant.io/integrations/recorder/) also requires free database temporary space at least the database size for rebuild/repack operations; confirm existing capacity evidence rather than triggering a purge/repack. Runtime backup and rollout evidence belong to the driver and were not obtained here.

Public raw source/diff evidence is under `/tmp/renovate-181-ha-review/`. Review complete; helper stops here.
