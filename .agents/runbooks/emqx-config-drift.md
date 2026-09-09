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
  core. That is survivable on 6.2.0 (transient 2-node cluster → the new node
  syncs Mnesia: users, retained messages, cluster.hocon) but it is a planned
  1–2 min MQTT outage, and it is the exact thing the 6.2.0 pin exists to avoid
  on 6.2.1+ (`SINGLE_NODE_LICENSE`, emqx/emqx#17600).
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

A new core node that fails to join the old one would come up with none of this.
On 6.2.0 the join works; watch `emqx ctl cluster status` on the new pod during a
blue-green.

## Housekeeping the operator does not do

Every blue-green leaves the previous StatefulSet at 0 replicas and, for some,
its PVC (`kubectl get sts,pvc -n database | grep emqx-core`). Delete the 0/0
StatefulSets and orphan PVCs once the new core has been stable for a day.

## Lessons from the 2026-09-09 window (read before any pod-template change)

**A pod-template change on this broker = a full MQTT outage, not a bounce.** The affinity
change (#2801) made the operator blue-green the core. What actually happened, with times:

| UTC | event |
|---|---|
| 14:17:33 | operator creates `emqx-core-8545588dbb` (new hash) on talosm02; pod Ready ~14:18 |
| 14:18:03 | operator: `failed to start node evacuation: error accessing emqx-core-5db6f9b9c6-0 API` (the Community edition has no rebalance/evacuation API) |
| 14:19:20 | operator scales the OLD StatefulSet to 0 anyway (`Delete Pod emqx-core-5db6f9b9c6-0`, PreStopHook failed) |
| 14:21 | new pod fails liveness (18083); every restart after that hangs in `mria_mnesia: still waiting for table(s): [cluster_rpc_mfa,cluster_rpc_commit]` / `Table cluster_rpc_mfa is waiting for one of the nodes: [old node]` — its Mnesia schema still lists the dead old core as a table holder |
| 14:19 → 14:30:45 | **MQTT down 12 min**: z2m/HA/AppDaemon disconnected |
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

**Next time** (any change to `spec.coreTemplate.spec`, `image`, or anything else in the pod
template):

1. Plan it as a ~10–15 min MQTT outage window, declared, with Tom's go.
2. Merge, then watch `kubectl get pod -n database -l apps.emqx.io/db-role=core -w`. The moment the
   NEW pod is Ready and BEFORE the operator kills the old one (it waited ~1m50s on 2026-09-09),
   detach the old node cleanly so the new one owns the data:
   `kubectl exec -n database <OLD pod> -c emqx -- emqx ctl cluster leave`
   — that removes the old node from the schema on both sides; the operator's later scale-down is
   then harmless. If you miss the window and the new pod loops on `waiting for table(s)`, do the
   fresh-PVC recovery above (fast, deterministic) rather than trying to resurrect the old pod.
3. After the new pod is Ready: re-check `emqx ctl conf show retainer` (fresh PVC = git's
   base.hocon values until the operator hot-applies or you PUT), then restart z2m so the retained
   discovery store is rebuilt, then the group `get` pokes.

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
