# Z-Wave end-device firmware: approved batches

Policy and evidence recorded on 2026-11-17 for [story 643][story643], under
[epic 640][epic640]. This document starts no update and enables no unattended
runner. The rules block is for [story 644][story644]; no program ships here.
(Story 643, comments 8838 and 8843; story 642, comment 8831.)

## 1. What this covers

This covers firmware of Z-Wave end devices, today Zooz ZSE44 and ZSE70 sensors.
Device firmware is one-way; there is no rollback. Zigbee and ESPHome devices have
their own stories. Radio bridges, Z-Wave and Zigbee controller firmware, and
garage door openers are outside this procedure. Never touch those without Tom at
home. (Epic 640, constraints; story 643, Q-547-05, comment 6711.)

## 2. Tom's rulings and the approval record

| Ruling | Date and place | Tom's recorded answer and effect |
|---|---|---|
| Q-547-05 | 2026-11-10, phone prompt; [story 643][story643], comment 6711 | **"Approved batches (Recommended)"**. Tom approves one named batch. Update one device at a time, plug-in devices before battery ones. Skip below 50% battery. Stop at the first problem. The excluded classes above require Tom at home. |
| Q-547-02 | 2026-11-10, answered shortly before 17:05Z, phone prompt; story 643, comment 6688 | **"dev-env v2 approval framework"**. That is the required approval route. The 2026-11-17 device work used a phone prompt in the device session, with the answer quoted on [story 642][story642], comment 8831. This records that use; it does not claim a general runner approval service is installed. |
| Q-640-01 | 2026-11-17, asked 18:04Z, answered by 20:30Z, phone prompt; [epic 640][epic640], comment 8834 | **"No, keep updating"**. Z-Wave JS UI keeps updating by itself. There is no app-update hold. Device work gives way to app updates. |
| Q-640-02 | 2026-11-17, asked 20:43Z, answered by 20:45Z, phone prompt; epic 640, comment 8840 | **"Yes, start it again (Recommended)"**. Only a waiting request dropped by an app restart may be started again, on the same approved device and version pair, with all the same checks. At most three such restarts per device per batch. Record each drop and start. An interrupted running transfer is a failure. |

Before the first start, write the named list and Tom's quoted approval on the
batch's story. Approval covers only that list. It gives no standing approval for
other devices or versions. (Epic 640, constraints and comment 8840; story 642,
comment 8831.)

## 3. The batch manifest

Record node, name, model, hardware version, and the exact from/to pair offered by
that device's own update entity. Match all of them before starting. Unknown
hardware is missing evidence, not a match. (Epic 640, comment 8810, compatibility
row; story 642, comment 8831.)

This is the approved list of 2026-11-17, in its fixed order. Tom answered at about
18:37Z: **"All 6, one at a time (Recommended)"**. The source is story 642,
comment 8831. It is a historical manifest, not a new approval or a live queue.

| Order | Node | Name | Model | Hardware version in source | Exact from | Exact to |
|---|---|---|---|---|---|---|
| 1 | 26 | `shed_temperature_sensor` | ZSE44 | 2 | 2.40.0 | 2.50.0 |
| 2 | 56 | `cigar_humidity_sensor_07` | ZSE44 | Not recorded | 2.40.0 | 2.50.0 |
| 3 | 18 | `cigar_humidity_sensor_01` | ZSE44 | Not recorded | 2.40.0 | 2.50.0 |
| 4 | 19 | `cigar_humidity_sensor_02` | ZSE44 | Not recorded | 2.40.0 | 2.50.0 |
| 5 | 55 | `cigar_humidity_sensor_06` | ZSE44 | Not recorded | 2.40.0 | 2.50.0 |
| 6 | 40 | `shed_indoor_motion_sensor` | ZSE70 800LR | Not recorded | 1.30.1 | 1.50.0 |

The device owner must supply the five missing hardware values on the batch record
before those starts pass compatibility. Do not infer them from a model name.
Node 17 is a separate completed example, not an extra member of this batch:
ZSE44, hardware 2, 2.40.0 to 2.50.0, read back on 2026-11-17 at 18:35Z.
(Epic 640, comment 8810; story 642, comments 8829 and 8831.)

## 4. Before each start

All checks apply again to a replacement request under Q-640-02.

1. Confirm the named batch approval and exact node, model, hardware and offered
   from/to pair. Check the never-touch and hold lists below. (Story 643,
   comment 6711; epic 640, comment 8810; story 642, comment 8831.)
2. For a battery device, require at least **50%**, from a battery report no older
   than **24 hours**. Check the report timestamp, not just the current entity
   value. Node 17's July reading was stale in November. (Epic 640, comment 8832,
   finding 3, and comment 8834.)
3. Require a scheduled wake in the last **24 hours**. Sensor reports alone do not
   prove a wake. Do not start in the first wake after a battery change or power
   cycle; S2 may still be resynchronizing. (Epic 640, comment 8832, findings 4–6.)
4. Ask the **driver**, through its state or log, whether anything is waiting or
   running anywhere on the network. Start nothing while it holds another request.
   Never use Home Assistant's `in_progress` to prove the network is free: a reload
   cleared it while the driver still held node 17's request. Do not test for a
   waiting request by starting another one. (Epic 640, comments 8810 and 8832,
   finding 2; story 642, comment 8821.)
5. Require no app-update PR open, and no merged app update waiting to be applied.
   Z-Wave JS UI must run the version on main and be settled. If an app update is
   on its way, wait for it to land and the app to return. Do not hold the app.
   (Epic 640, Q-640-01 and Q-640-02, comments 8834 and 8840.)
6. Do not start in the **30 minutes before local midnight**, in
   `America/New_York`. The app backs up controller memory and turns RF off for
   about half a minute at midnight. This start margin does not promise that a
   sleeping device will transfer before midnight. (Epic 640, comment 8818.)

A failed check stops the batch and is reported. An app update on its way defers
the start until check 5 passes. (Story 642, comment 8831; epic 640, comment 8840.)

## 5. Start once, then wait within a bound

After the checks pass, issue one initial start for the device. Record its time
and the driver's start time. A sleeping node takes the request at its next wake;
pre-transfer validation can wait for that wake. (Story 642, comments 8821 and
8831; epic 640, comment 8840.)

| Model | Wake interval in today's device table | Wait bound from the request start |
|---|---|---|
| ZSE44 | 12 hours | 13 hours |
| ZSE70 | 4 hours | 5 hours |

The bound is the wake interval plus one hour. If no wake arrives inside it,
report and leave the request where it is. Do not issue another start because it
timed out. The only replacement-start exception is section 11. (Epic 640,
comment 8832, finding 4; story 642, comment 8831; epic 640, Q-640-02.)

## 6. Waking by hand: use the model's press

Checked against the maker's public manuals on 2026-11-17. These are wake
instructions and warnings about other presses, not permission to include,
exclude or reset a device.

| Model | Wake | Other presses to avoid | Manual |
|---|---|---|---|
| ZSE44 | Four quick taps of the Z-Wave button. One LED flash confirms wake; the radio listens for about one minute. This also worked on node 17 on 2026-11-17. | Three quick taps invokes inclusion/exclusion when the hub is in that mode. Factory reset uses two taps, a third press held for 10 seconds, then two more taps to finish. | [Zooz ZSE44 product manual][zse44manual], Wake-Up Mode, Z-Wave Control, Exclusion and Factory Reset. |
| ZSE70 800LR | One press of the Z-Wave button; the radio listens for about 10 seconds. This manual check does not claim a witnessed ZSE70 wake on 2026-11-17. | Three quick taps invokes inclusion/exclusion when the hub is in that mode. Holding the button for at least 10 seconds is factory reset. | [Zooz ZSE70 product manual][zse70manual], Wake-Up Mode, Z-Wave Control, Exclusion and Factory Reset. |

On node 17, slower presses produced an Indicator report rather than a wake-up
notification. Look for the wake notification. (Story 642, comment 8829; epic 640,
comment 8832, finding 5.)

Two source differences matter. Story 642, comment 8831 says **"or sooner if Tom
taps it 4 times"** for the batch, including ZSE70. Its manual specifies one
press; use the model-specific instructions above. Epic 640, comment 8832 calls
ZSE44 reset **"2 taps plus a 10 s hold"**. Its manual also requires two final
taps; the shorthand omits that step. All wake, inclusion/exclusion and reset
presses in the table were checked; none is left marked unchecked.

## 7. During the transfer

Leave the transfer alone. Never cancel it. On node 17 a ZSE44 transfer sent
**5,625 fragments** in about ten minutes (18:24:48–18:34:17Z). A short S2 resync,
duplicate frames and two failed Transport Service TX sessions at 18:31:31–37Z
recovered by themselves. One bad minute is not a failure. A reported failed or
aborted transfer is a stop. (Epic 640, comments 8810 and 8832, findings 6–7;
story 642, comments 8829 and 8831.)

## 8. The receipt that permits the next device

Write one receipt per device on the batch story. Include:

- Node, name, model, hardware, approved from/to pair and approval reference.
- Initial start time, driver start time, transfer outcome, every app-restart drop
  and every replacement start with its count.
- Version read back from the device's Version CC in the post-update interview.
- Home Assistant's device `sw_version` and update entity: off, with installed
  version equal to the approved target and latest offered version.
- Node back to its normal state: asleep for a sleeping sensor, or alive as
  appropriate. Include fresh, timestamped sensor readings and battery evidence.

Only then move to the next device. Sources: epic 640, comment 8810 (manifest and
receipt rows), comment 8832 (finding 7), comment 8840 (drop/start record); story
642, comments 8829 and 8831.

Node 17's receipt is the example: Version CC `firmwareVersions: 2.50`, hardware
2; HA `sw_version` 2.50.0; update entity off, installed = latest = 2.50.0;
normal sleep; temperature and humidity reported at 18:34:41Z; battery 100%.
The next periodic wake was still unverified in that receipt. Its low-battery
binary sensor was already unavailable before the update. Record such limits;
do not call an old unavailable entity a new regression. (Story 642, comment 8829.)

## 9. Stop and report

Stop the batch on a failed or aborted transfer, a node going dead, a failed
precheck, or no wake inside the model's bound. Leave a waiting request in place.
Do not retry a failed transfer or cancel one. Report on the batch story with
the last observed state, times, missing evidence and next safe step. A restart
that interrupts an actual transfer is a failure. A restart that drops only a
waiting request follows the bounded exception in section 11. (Story 642,
comment 8831; epic 640, comments 8810, 8832 and 8840.)

## 10. App restarts and the driver

Node 8's request waited from 2026-11-11 14:35:55Z until the app restarted on
2026-11-17 at about 04:08Z. The waiting request disappeared without a completion,
failure or drop line. While it waited, the nightly controller soft reset was
refused with **"A firmware update is in progress on this network"**. That refusal
is useful evidence that the driver still holds a request. Its absence alone
does not prove the network is free. (Epic 640, comment 8818; story 642,
comment 8821.)

While waiting, compare the driver's start time with the recorded one at every
look. After every app restart, ask the driver again whether it still holds the
request. Never substitute Home Assistant's `in_progress`. Keep app updates
running under Q-640-01. (Epic 640, comments 8832, 8834 and 8840.)

## 11. Q-640-02: replacement of a dropped waiting request

The earlier rule was **"that is not re-queued without Tom"** (story 642,
comment 8831). Epic 640, comment 8834 and story 643, comment 8838 left that
question open. Q-640-02 subsequently answered it on 2026-11-17 at 20:45Z
(epic 640, comment 8840); story 643, comment 8843 records the changed work order.

Only when the driver confirms that an app restart dropped a **waiting** request
may it be started again without another question. Keep the same approved device
and exact version pair. Repeat every precheck. Allow at most **three replacement
starts per device per batch**, beyond the one initial start. Record every drop
and new start. A fourth drop stops the batch and is reported. This gives no
retry for a failed, aborted or timed-out transfer, and none for a transfer that
was running at restart. (Q-640-02, epic 640, comment 8840; story 642,
comment 8842.)

## 12. Never-touch and hold lists

The never-touch classes are radio bridges, Z-Wave controller firmware, Zigbee
controller firmware and garage door openers. They require Tom at home and are
outside this batch procedure. (Q-547-05, story 643, comment 6711.)

| Held node or class | Why | Next safe step |
|---|---|---|
| 8, `server_room_temperature_sensor_01` (ZSE44) | Silent since 2026-11-09; old 37% reading | Physical check with Tom. Firmware is not the repair. |
| 9, `shed_ebike_power_switch` (ZEN15) | Dead plug-in | By hand, with Tom. |
| 21, `shed_outdoor_double_plug_01` (ZEN14) | Dead plug-in | By hand, with Tom. |
| 42, `front_yard_left_double_plug` (ZEN14) | Dead plug-in | By hand, with Tom. |
| 43, `front_yard_right_double_plug` (ZEN14) | Dead plug-in | By hand, with Tom. |
| 63, `shed_mombike_power_switch` (ZEN15) | Dead plug-in | By hand, with Tom. |
| All safety-related devices: locks, smoke sensors, water sensors and the garage | Safety-related | By hand, with Tom; no batch runner start. |

Sources: story 642, comment 8821 (node 8 and five dead nodes); epic 640,
comment 8810 (manual hold row) and comment 8832 (node 8). Node 17's former
stuck request completed; it is not another dead-node hold (story 642,
comment 8829). Missing hardware in section 3 still fails compatibility.

## 13. Rules for story 644's program

Story 644 will lift this JSON into its rules file. This is policy data, not an
installed or enabled runner. The source map applies to the named fields; the
2026-11-17 manifest is the historical table above. (Story 643, comments 8838 and
8843.)

```json
{
  "scope": "zwave_end_device_firmware",
  "models": {
    "ZSE44": {"wake_interval_hours": 12, "wait_bound_hours": 13},
    "ZSE70": {"wake_interval_hours": 4, "wait_bound_hours": 5}
  },
  "approval": {"named_batch_required": true, "standing_approval": false},
  "compatibility_fields": ["node", "name", "model", "hardware_version", "from_version", "to_version"],
  "exact_pair_must_be_offered_by_device_update_entity": true,
  "unknown_compatibility_blocks_start": true,
  "battery_floor_percent": 50,
  "battery_reading_max_age_hours": 24,
  "scheduled_wake_max_age_hours": 24,
  "start_on_first_wake_after_power_cycle": false,
  "max_waiting_or_running_devices": 1,
  "request_state_source": "driver",
  "use_ha_in_progress_for_exclusion": false,
  "device_order": "plug_in_before_battery",
  "initial_starts_per_device_per_batch": 1,
  "cancel_transfer": false,
  "wake_timeout_action": "report_leave_request_no_second_start",
  "timezone": "America/New_York",
  "no_start_minutes_before_midnight": 30,
  "hold_app_updates": false,
  "app_start_checks": ["no_open_app_update_pr", "no_merged_unapplied_app_update", "running_version_matches_main", "app_settled"],
  "waiting_check": "compare_driver_start_time_at_every_look",
  "after_app_restart": "ask_driver_again",
  "dropped_waiting_request": {
    "replacement_start_allowed": true,
    "cause_required": "app_restart",
    "same_approved_device_and_version_pair": true,
    "repeat_all_prechecks": true,
    "max_replacement_starts_per_device_per_batch": 3,
    "stop_on_drop_number": 4,
    "record_every_drop_and_start": true
  },
  "interrupted_running_transfer": "stop_report_no_retry",
  "never_touch_without_tom_home": ["radio_bridges", "zwave_controller_firmware", "zigbee_controller_firmware", "garage_door_openers"],
  "hold_nodes": [
    {"node": 8, "reason": "silent_since_2026-11-09_physical_check"},
    {"node": 9, "reason": "dead"},
    {"node": 21, "reason": "dead"},
    {"node": 42, "reason": "dead"},
    {"node": 43, "reason": "dead"},
    {"node": 63, "reason": "dead"}
  ],
  "hold_classes": ["all_safety_related_devices", "locks", "smoke_sensors", "water_sensors", "garage"],
  "hold_action": "by_hand_with_tom",
  "stop_on": ["failed_or_aborted_transfer", "node_dead", "failed_check", "no_wake_inside_bound", "fourth_dropped_waiting_request", "running_transfer_interrupted_by_app_restart"],
  "receipt_required_before_next_device": true,
  "sources": {
    "scope_and_approval_and_order_and_battery_floor_and_never_touch": "story 643 comments 6688, 6711; story 642 comment 8831",
    "compatibility_and_holds_and_no_cancel": "epic 640 comment 8810; story 642 comment 8821",
    "freshness_and_wake_and_driver_and_power_cycle": "epic 640 comment 8832 findings 2-6; comment 8834",
    "wake_bounds_and_stops_and_receipt": "story 642 comments 8829, 8831; epic 640 comment 8832 findings 4, 7",
    "midnight": "epic 640 comment 8818",
    "app_checks_and_replacement_exception": "epic 640 comments 8834, 8840 (Q-640-01, Q-640-02)"
  }
}
```

## 14. Where the facts come from

The driver writes `store/logs/zwavejs_*.log` in the app's store. It logs in
New York local time (UTC−5 on the dates below), with about six days retained in
the 2026-11-17 observation. Older restart history was unavailable. The quotes
below are copied only from epic 640, comment 8818 and story 642, comment 8829;
they are not newly read logs.

| Line to look for | What the recorded observation establishes |
|---|---|
| `[Node 008] OTA firmware update started, downloading 1 updates...` | Node 8's request began on 2026-11-11 at 09:35:55 local. A start is not proof of transfer or completion. |
| `Failed to soft reset controller: A firmware update is in progress on this network` | Nightly resets refused while the driver held the request, 12–16 November. |
| `DRIVER version 15.32.3` | Driver start after the app restart, 2026-11-16 at 23:08:40 local. |
| `Performing soft reset...` | Reset ran on 2026-11-17 at 00:00:27 local, after the waiting request disappeared without an end line. |
| `Turning RF off...` | Midnight controller backup turns RF off for about half a minute, even during device work. |
| `Device IDs match, installing firmware updates…` | Node 17's validation passed before the fragments started. |
| `Firmware updated, scheduling interview in 5 seconds…` | Node 17 reached post-update interview; still read back its version and record the receipt. |

Story 642, comment 8829 also records wake-up notifications, fragment progress,
the S2 recovery, Version CC readback and fresh readings. Those distinguish a
waiting request from a transfer and a completed update from a mere start.

[story643]: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/643
[epic640]: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/640
[story642]: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/642
[story644]: https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/644
[zse44manual]: https://www.support.getzooz.com/kb/article/2310-zse44-temperature-humidity-sensor-product-manual/
[zse70manual]: https://www.support.getzooz.com/kb/article/2360-zse70-outdoor-motion-sensor-product-manual/
