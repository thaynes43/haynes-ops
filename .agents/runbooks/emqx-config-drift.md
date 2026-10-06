# EMQX config drift — when the running broker ignores the CR

**Symptom class:** `kubernetes/main/apps/database/emqx/cluster/cluster.yaml`
(`spec.config.data`) says one thing, `emqx ctl conf show <root>` says another.
Seen 2026-09-09: git `retainer.max_payload_size = 4MB`, broker enforced `1MB`
(`retain_failed_for_payload_size_exceeded_limit` on `zigbee2mqtt/bridge/devices`,
3.7 MB), and Home Assistant's `homeassistant/#` resubscribe was aborted with
`retained_fetch_rate_limit_exceeded` (~7,400 retained discovery configs against
the default `delivery_rate = 1000/s`). Broker: EMQX 6.2.0, single core node,
emqx-operator 2.x, namespace `database`, pod `emqx-core-<hash>-0`.

## Why it happens — the four layers

From the broker's own boot script (`/opt/emqx/bin/emqx`, `generate_config()`),
lowest priority first:

1. `/opt/emqx/etc/base.hocon` — the operator renders `spec.config.data` here
   (ConfigMap `emqx-configs`). Read at boot only.
2. **`/opt/emqx/data/configs/cluster.hocon`** — on the PVC
   (`emqx-core-data-emqx-core-<hash>-0`). Header: *"results of online config
   changes from UI/API/CLI"*. Every hot change lands here: dashboard edits,
   `PUT /api/v5/...`, `emqx ctl conf load`, **and the operator's own hot-apply**.
   It outlives pod restarts, and a new core node created by a blue-green
   receives it through cluster config sync when it joins the old one.
3. `/opt/emqx/etc/emqx.conf` — the operator ships it **empty**.
4. `EMQX_*` environment — only cluster/dashboard/node keys today.

Once a key exists in `cluster.hocon`, `base.hocon` (= git) never wins for that
key again. There is no "reset to CR" in the operator.

**How 4MB became 1MB (forensics 2026-06-05).** `emqx ctl conf cluster_sync status`
showed three transactions in the same second: the operator's hot-apply
(`PUT /api/v5/configs?mode=merge`, fired whenever `spec.config.data` differs from
the CR annotation `apps.emqx.io/last-emqx-configuration`) wrote `mqtt` and
`retainer` with 4MB, then a third transaction rewrote `retainer` with the schema
default `1MB`. `cluster.hocon.2026.06.05.14.02.28.746.bak` next to the live
file still holds the 4MB version. Cause of the third write unknown (EMQX-side
full-object rewrite); the lesson is that a CR edit alone is not a reliable way
to change a hot-configurable key.

## Diagnose (read-only)

```bash
POD=$(kubectl get pod -n database -l apps.emqx.io/db-role=core -o jsonpath='{.items[0].metadata.name}')
# effective config
kubectl exec -n database $POD -c emqx -- emqx ctl conf show retainer
kubectl exec -n database $POD -c emqx -- emqx ctl conf show mqtt | grep max_packet_size
kubectl exec -n database $POD -c emqx -- emqx ctl conf cluster_sync status
# the layers
kubectl exec -n database $POD -c emqx -- grep -nE 'max_payload_size|delivery_rate|max_publish_rate|max_packet_size' /opt/emqx/etc/base.hocon /opt/emqx/data/configs/cluster.hocon
# the API view (the bootstrap key lives in the pod; never copy it out)
kubectl exec -n database $POD -c emqx -- sh -c 'K=$(cat /opt/emqx/etc/bootstrap_api_keys); curl -s -u "$K" http://localhost:18083/api/v5/mqtt/retainer'
kubectl exec -n database $POD -c emqx -- sh -c 'K=$(cat /opt/emqx/etc/bootstrap_api_keys); curl -s -u "$K" http://localhost:18083/api/v5/configs/global_zone' | grep -o '"max_packet_size":"[^"]*"'
```

The bootstrap API key file is `/opt/emqx/etc/bootstrap_api_keys` (key name
`emqx-operator-controller`, from Secret `emqx-bootstrap-api-key`); the dashboard
API is `http://localhost:18083`. Read it inside the pod with `$(cat ...)` as
above so the secret never reaches a log or a transcript.

Signals in the broker log (`kubectl logs -n database $POD`), and what they mean:

| log line | meaning | fix lives in |
|---|---|---|
| `retain_failed_for_payload_size_exceeded_limit ... limit: N` | a retained publish is bigger than `retainer.max_payload_size` — it is delivered live but **not stored** | broker (`retainer.max_payload_size`) |
| `retained_fetch_rate_limit_exceeded` / `failed_to_consume_from_limiter,{emqx_retainer,dispatcher}` | a subscriber's retained fetch was **aborted** by `retainer.delivery_rate` — it silently misses the rest | broker (`retainer.delivery_rate`) |
| `frame_is_too_large` + `msg: packet_is_discarded` | EMQX's **outbound** serializer dropped a message because the *client* declared a smaller MQTT 5 Maximum Packet Size. Check `GET /api/v5/clients/<id>` → `send_msg.dropped.too_large`. For zigbee2mqtt it is its own 1 MiB default (`mqtt.maximum_packet_size`), and it drops its own 3.7 MB `bridge/devices` echo | the client (z2m: `ZIGBEE2MQTT_CONFIG_MQTT_MAXIMUM_PACKET_SIZE`) |
| `log_events_throttled_during_last_period ... dropped: #{retained_fetch_rate_limit_exceeded => N}` | the throttler hid N more of the above | — |

`mqtt.max_packet_size` shows as `268435455B` (the protocol maximum) — that is
256MB working, not a bug.

## Fix — runtime first, then git

1. **Change the running broker.** Use the API with the **whole** object:
   `PUT /api/v5/mqtt/retainer` (and `PUT /api/v5/configs/global_zone` for
   `mqtt.*`) replace the object, so omitted fields go back to defaults — that is
   exactly the 4MB→1MB accident. GET, edit, PUT:

   ```bash
   kubectl exec -n database $POD -c emqx -- sh -c 'K=$(cat /opt/emqx/etc/bootstrap_api_keys); curl -s -u "$K" http://localhost:18083/api/v5/mqtt/retainer' > /tmp/retainer.json
   # edit /tmp/retainer.json (max_payload_size, delivery_rate, max_publish_rate ...)
   kubectl exec -i -n database $POD -c emqx -- sh -c 'K=$(cat /opt/emqx/etc/bootstrap_api_keys); curl -s -w "HTTP %{http_code}\n" -u "$K" -X PUT -H "Content-Type: application/json" http://localhost:18083/api/v5/mqtt/retainer -d @-' < /tmp/retainer.json
   ```

   CLI alternative that only touches the keys you name:

   ```bash
   kubectl exec -n database $POD -c emqx -- sh -c 'printf "retainer { max_payload_size = 8MB, delivery_rate = infinity, max_publish_rate = infinity }\n" > /tmp/patch.hocon && emqx ctl conf load --merge /tmp/patch.hocon'
   ```

   (`--merge` overlays; `--replace` would wipe unlisted keys.) Both persist into
   `cluster.hocon` and survive restarts and blue-greens.

2. **Verify** with the diagnose commands: `conf show`, the API, and
   `cluster.hocon` must all agree.

3. **Make git say the same thing** in `cluster.yaml` `spec.config.data`, with a
   comment that names the date and the API call. Know what that commit does:
   the operator hot-applies the diff via the API (a no-op if step 1 already
   matched it), **and** the regenerated `emqx-configs` ConfigMap trips
   `reloader.stakater.com/auto` on the core StatefulSet → the single core pod
   **restarts** (≈1 min MQTT bounce, zigbee2mqtt reconnects, AppDaemon logs
   MQTT reconnects). Treat any `spec.config.data` edit as a broker restart and
   declare it. After the restart re-run `conf show` — if the value has snapped
   back to a default (the 2026-06-05 pattern), repeat step 1 and note it here.

4. **After a broker restart, make the Zigbee side whole:** zigbee2mqtt's LWT can
   land late and mark every entity unavailable (memory
   `z2m-lwt-race-after-abrupt-node-loss`) → `kubectl rollout restart deploy/zigbee2mqtt
   -n home-automation`; then groups need a state poke before HA shows them:
   publish `zigbee2mqtt/<group friendly_name>/get` with `{"state":""}` for each
   group (list them from the retained `zigbee2mqtt/bridge/groups`). The API key
   can publish: `POST /api/v5/publish {"topic":"zigbee2mqtt/<group>/get","payload":"{\"state\":\"\"}"}`.

## Do not

- Do not add `env` to `spec.coreTemplate.spec` to force a value: env *is* part of
  the pod template, and any pod-template change makes the operator blue-green the
  core. On operator 2.x that is a **full MQTT outage**, on 6.2.0 as well as on
  6.2.1+. The new node never joins: the Community license aborts it with
  `SINGLE_NODE_LICENSE` (emqx/emqx#17600). The outage ends only with the
  fresh-PVC recovery in *Next time* below.
- Do not set `replicas: 3` — single-node COMMUNITY license.
- Do not hand-edit `cluster.hocon` on the PVC; use the API/CLI so the change
  goes through `cluster_sync`.
- Do not copy `bootstrap_api_keys` out of the pod.

## What lives only in Mnesia (why blue-green data sync matters)

- `authentication` users (`password_based:built_in_database`): the init user is
  re-created from `/opt/init-user.json` at boot; any other user only exists in
  Mnesia.
- `authorization` `built_in_database` rules: **zero** today — every client is a
  superuser, `no_match = deny` is not doing anything.
- Retained messages: ~7,450 (7,243 under `homeassistant/`).
- `cluster.hocon` itself.

A new core node that fails to join the old one comes up with none of this, and on
the Community license it always fails. **Corrected 2026-10-06:** this runbook used to
say that "on 6.2.0 the join works". The 2026-09-09 broker logs in Loki show the
opposite. The new 6.2.0 node aborted with `SINGLE_NODE_LICENSE` at its first join
attempt and crash-looped. So a blue-green on this broker never carries the data
across; plan for the fresh-PVC recovery.

## Housekeeping the operator does not do

Every blue-green leaves the previous StatefulSet at 0 replicas and, for some,
its PVC (`kubectl get sts,pvc -n database | grep emqx-core`). Delete the 0/0
StatefulSets and orphan PVCs once the new core has been stable for a day.

## Lessons from the 2026-09-09 window (read before any pod-template change)

**A pod-template change on this broker = a full MQTT outage, not a bounce.** The affinity
change (#2801) made the operator blue-green the core. What actually happened, with times:

| UTC | event |
|---|---|
| 14:17:33 | operator creates `emqx-core-8545588dbb` (new hash) on talosm02 |
| 14:17:52 | the new 6.2.0 node finds the old one through the headless service (`publishNotReadyAddresses: true`) and starts joining (`reason: join, Stopping mria`) |
| 14:17:53 | the new node aborts with `application_start_failure,emqx_license,"SINGLE_NODE_LICENSE ..."` and crash-loops: the same abort at the 14:17:59, 14:18:19 and 14:18:50 restarts. **It is never Ready.** (Re-read from Loki 2026-10-06; this row used to say "pod Ready ~14:18".) |
| 14:18:03 | operator's evacuation call errors (`HTTP 400 ... Nodes unavailable: [new node]`), but the OLD node logs `node_evacuation_started` |
| 14:19:03 | the old node evicts its 3 connections (z2m, HA, AppDaemon) after evacuation's 60 s health wait. **The MQTT outage starts here.** |
| 14:19:16 | operator scales the OLD StatefulSet to 0; the old pod gets SIGTERM and its listeners stop |
| 14:19:57 → | the new pod (restart 4) hangs in `mria_mnesia: still waiting for table(s): [cluster_rpc_mfa,cluster_rpc_commit]`, `Check down_nodes ... got [old node]`. The half-finished join wrote the dead old core into its Mnesia schema, so it never recovers on its own |
| 14:19:03 → 14:30:45 | **MQTT down 12 min**: z2m/HA/AppDaemon disconnected |
| 14:30 | recovery: `kubectl delete pvc emqx-core-data-emqx-core-8545588dbb-0 --wait=false` + `kubectl delete pod emqx-core-8545588dbb-0` → fresh data dir → node boots standalone (DNS discovery finds only itself) → Ready in 15 s |
| 14:31 | retainer values re-applied via the API (fresh `cluster.hocon`); z2m/HA/AppDaemon reconnected on their own |
| 14:39 | z2m restart (#2802) republished the ~7,100 retained discovery configs; 30 group `get` pokes via `POST /api/v5/publish` |

The old PVC (`emqx-core-data-emqx-core-5db6f9b9c6-0`) was left untouched: it holds the pre-window
retained store, the `cluster.hocon`, and the Mnesia schema, if anything ever needs to be dug out.

What the fresh data dir cost: the retained store (rebuilt by the z2m restart, ~7,100 of the
~7,450 messages came back — the rest were stale configs for removed devices plus a handful of
non-z2m retained topics that reappear when their publishers next publish) and the dashboard's
API keys created by hand, if any. Users: only the bootstrapped `admin`, which came back from
`/opt/init-user.json`. Authz rules: none existed.

## The 2026-10-06 window (haynes-ops#3384, requests and probe timeouts): what actually happened

The plan above was based on 2026-09-09. Here is how 2026-10-06 differed:

| UTC | event |
|---|---|
| 00:48:30 | Flux applies the CR. The operator creates `emqx-core-f5c7475d4` on talosm05. Its pod crash-loops on `SINGLE_NODE_LICENSE` about 9 s after each start, **before its first readiness probe** (initialDelay 10 s). |
| 00:48 → 01:31 | **The operator never moves.** Its scale-down gate (`checkInitialDelaySecondsReady`) needs the EMQX `Available` condition, and that needs a Ready pod in the new StatefulSet. On 2026-09-09 the new pod was briefly Ready before it crashed, which is the only reason the operator went on to evict and delete the old one. The old core kept serving (no outage), and `emqx ctl cluster status` on it listed the half-joined new node as `stopped`. |
| 01:31:43 | The old StatefulSet is scaled to 0 by hand. The dev-env SA is denied `statefulsets/scale`; this was a one-off `kubectl patch sts emqx-core-8545588dbb --type merge -p '{"spec":{"replicas":0}}'` with Tom's explicit approval. |
| 01:31:48 | Old pod gone. A watcher loop does the fresh-PVC step on the new pod within 1 s. |
| 01:32:07 | New core Ready, standalone. **Broker down for about 23 s.** |
| 01:32:18 | zigbee2mqtt reconnected on its own. |
| 01:35:16-17 | Home Assistant and AppDaemon reconnected. HA's event loop sat at about 1 core for 3 minutes, processing about 6,500 MQTT entities going unavailable, and its websocket clients logged "unable to keep up". AppDaemon's MQTT plugin connected at 01:32:16 but could not re-initialise until HA was back. |
| 01:35:54 → 01:36:07 | z2m restarted. Retained messages went from 7 to 7,229. Then the 30 group `get` pokes. MQTT entities unavailable: 6, the same as before the window. The operator then marked the CR Ready on revision f5c7475d4, and the old StatefulSet stayed at 0. |

Side effect: the `zigbee2mqtt` Flux Kustomization `dependsOn` `emqx-cluster`. Both reported Not Ready while
the cutover stalled, so `FluxReconciliationFailure` fired for **both**. Silence both, or reconcile
quickly.

**Next time** (any change to `spec.coreTemplate.spec`, `image`, or anything else in the pod
template, while we are on operator 2.x):

1. Plan it as an MQTT outage window with someone at the keyboard, and declare it
   (`declare-activity start ... --scope database,emqx,home-automation,zigbee2mqtt,home-assistant`).
   Get Tom's go, **including his explicit OK for step 3's StatefulSet patch**, or have him run the
   scale himself. Silence `EMQXCoreNotReady`, plus `FluxReconciliationFailure` for `emqx-cluster`
   and `zigbee2mqtt`.
2. Merge and reconcile, then watch
   `kubectl get pod -n database -l apps.emqx.io/db-role=core -o wide -w`. Expect the new pod to
   crash-loop on `SINGLE_NODE_LICENSE` while the old one keeps serving. Do **not** try
   `emqx ctl cluster leave`: the new node never finishes its join, so there is no clean
   hand-over to make.
3. **Do not wait for the operator.** It may evict and delete the old core within about 2 min,
   as on 2026-09-09, or it may wait forever, as on 2026-10-06. As soon as the new pod is
   crash-looping, scale the OLD StatefulSet to 0 yourself. That is the single step the
   operator would otherwise take.
4. The moment the OLD pod is gone (and its StatefulSet is at 0, so it cannot come back), give
   the new pod a fresh data dir:
   `kubectl delete pvc -n database emqx-core-data-<new pod> --wait=false && kubectl delete pod -n database <new pod>`.
   Do not do this before the old pod is gone: the recreated new pod would find it and join
   again. The StatefulSet recreates the pod with an empty PVC. DNS discovery finds only itself,
   so it boots standalone; it was Ready in 19 s on 2026-10-06. A polling loop that issues this
   the second the old pod disappears holds the broker outage to about 25 s.
5. After it is Ready, run `emqx ctl conf show retainer`. A fresh PVC means git's `base.hocon`
   values, which are the intended ones since 2026-09-09 (8MB / infinity / infinity); PUT them if
   not. z2m reconnects within seconds; HA and AppDaemon can take about 3 min (above). Then
   `kubectl rollout restart deploy/zigbee2mqtt -n home-automation` so the retained discovery
   store is rebuilt, followed by the group `get` pokes (step 4 of *Fix* above). Neither
   switches a light.
6. A day later, delete the old 0/0 StatefulSet and its PVC (*Housekeeping* above).

**The operator's hot-apply is fragile.** After #2800 every reconcile logged
`failed to update emqx config through API ... HTTP 400 parse_error "syntax error before: \"\""`
— the `#` comment lines inside `spec.config.data` broke the operator's re-serialized HOCON,
so the CR annotation `apps.emqx.io/last-emqx-configuration` never advanced and the reconcile
loop stayed in error (later sub-reconcilers such as stale-StatefulSet cleanup do not run while
it errors). Keep `config.data` plain HOCON — no comments, quote strings like `"infinity"` — and
put the commentary in YAML comments above `config:`. Check with
`kubectl logs -n database deploy/emqx-operator-controller-manager -c manager | grep '"level":"error"'`.

**Verified clean after the window:** z2m connects with `maximum_packet_size: 10485760`
(retained `zigbee2mqtt/bridge/info` → `config.mqtt`), `send_msg.dropped.too_large: 0` on every
client, no `frame_is_too_large` / `retain_failed_*` / `retained_fetch_rate_limit_exceeded` in the
broker log, HA `subscriptions_cnt: 488`.

## The operator's hot-apply is disabled on purpose (2026-09-09)

`cluster.yaml` pins `metadata.annotations["apps.emqx.io/last-emqx-configuration"]` to
`spec.config.data` with a YAML anchor. Why: emqx-operator 2.3.1 (`internal/controller/sync_emqx_config.go`)
compares that annotation with `spec.config.data`; on a difference it parses the config with
go-hocon, re-prints it (`internal/controller/config/emqx.go` `printObject`: root keys joined by
`, `, empty strings as `""`) and PUTs it to `/api/v5/configs?mode=<spec.config.mode>`. On this
config EMQX 6.2.0 answers `HTTP 400 parse_error "syntax error before: \"\""` — while the raw
`base.hocon` PUT by hand gets `HTTP 200` — so the loop errored on every reconcile and the
annotation never advanced. The same code path produced the 2026-06-05 tnx3 that reset
`retainer.max_payload_size` to 1MB. Which token the printer mangles was not identified (no
Go here; the operator only logs the printed config at debug level).

With the annotation pinned, the operator sees nothing to apply and stays green. That means:
- a `spec.config.data` change reaches the broker only through `base.hocon` at the reloader
  restart, and only for keys NOT already present in `cluster.hocon` (the PVC override layer);
- so for runtime-tunable keys (`retainer.*`, `mqtt.*`, listeners, …) apply through the API or
  `emqx ctl conf load --merge` FIRST, then commit the same to git (the annotation follows via the
  anchor) — exactly the "runtime first, then git" procedure above;
- if you ever want the hot-apply back (a fixed operator), delete the annotation block and the
  anchor; the operator records the current spec on its next reconcile and applies later diffs.
