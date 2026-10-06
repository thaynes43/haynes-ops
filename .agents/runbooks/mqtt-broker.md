# MQTT broker (Mosquitto): layout, clients, `MosquittoNotReady`

The house MQTT broker is **Mosquitto 2.1**, one pod, no operator. Every Zigbee light
depends on it: zigbee2mqtt talks to the Zigbee radio and to Home Assistant only
through MQTT.

It replaced EMQX on 2026-10-06 (haynes-ops#3395). EMQX's free licence allows a
single node, and emqx-operator 2.x rolled out every pod-template change as a
blue-green that needed a second node. The licence aborted that second node
(`SINGLE_NODE_LICENSE`), so every image bump, resource or probe change was a
hand-run outage window. Mosquitto restarts in about a second, so those changes are
now ordinary rolling restarts.

## Where it lives

| piece | value |
|---|---|
| manifests | `kubernetes/main/apps/database/mosquitto/` (bjw-s app-template, Flux Kustomization `mosquitto` in `database`) |
| pod | StatefulSet `mosquitto`, pod `mosquitto-0`, namespace `database`, prefers the bare-metal masters |
| address | Service `mqtt`: `mqtt://mqtt.database.svc.cluster.local:1883` (plain MQTT, no TLS, no websockets); also LoadBalancer **192.168.40.205** on the LAN, the IP EMQX's `emqx-listeners` had |
| storage | PVC `data-mosquitto-0` (ceph-block, 1Gi): `mosquitto.db`, the retained store, saved every 60 s and on SIGTERM |
| config | `app/resources/mosquitto.conf` → ConfigMap `mosquitto-configmap`; a change restarts the pod (reloader) |
| auth | ExternalSecret `mosquitto` → Secret `mosquitto-secret` (plaintext `passwd`) → initContainer `hash-passwords` hashes a copy into an emptyDir with `mosquitto_passwd -U` |
| alert | PrometheusRule `mosquitto` → `MosquittoNotReady` (critical, 2 min) |

The Service has a broker-neutral name on purpose. To move the clients to another
broker later, point `mqtt` somewhere else instead of editing every client.

## Clients

| client | configured in | notes |
|---|---|---|
| zigbee2mqtt | `home-automation/zigbee2mqtt/app/helmrelease.yaml` `ZIGBEE2MQTT_CONFIG_MQTT_SERVER`; user/password from `zigbee2mqtt-secret` | Publishes ~7,200 retained messages: HA discovery configs under `homeassistant/`, device state under `zigbee2mqtt/`. Its Flux Kustomization `dependsOn` `mosquitto`. |
| Home Assistant | MQTT config entry `01JBDHVYGQ8V7PK299GSMZ28BZ` (UI-managed, `.storage/core.config_entries`) | Change the broker with the entry's **reconfigure** flow, never YAML, and only while HA is idle (see *Moving Home Assistant* below). The password stays as stored. |
| AppDaemon | `home-automation/appdaemon/app/externalsecret.yaml`: `MQTT_HOST` (literal) plus `MQTT_USER` / `MQTT_PASSWORD` from the `appdaemon` 1Password item | The health-check app's `MqttBrokerChecker` round-trips `appdaemon/health_check/ping`, so a broker problem shows up as that checker going critical. |
| ESPHome | `home-automation/esphome/app/externalsecret.yaml` `mqtt_host` | No device config uses MQTT (checked 2026-10-06: only `secrets.yaml` names it). LAN devices could not resolve cluster DNS anyway. |

Between 2026-09-06 and 2026-10-06, EMQX never had more than these three live
clients, apart from one 2-minute fourth connection on 2026-09-11 at 00:58Z.

In the broker log, a connection from a node IP every 5-10 s that closes without a
CONNECT is the kubelet's TCP probe, not a client.

### Moving Home Assistant to another broker

Use the HA MCP: `ha_set_integration(entry_id="01JBDHVYGQ8V7PK299GSMZ28BZ",
reconfigure=True, config={"broker": "<host>"})`. Run it once as a preflight, then
again with the returned `confirm_token`. The call goes over ha-mcp's websocket, and
it **times out while HA's event loop is busy**. That happened twice on 2026-10-06.
Fallback: drive the same flow over HA's REST API from inside the AppDaemon pod, whose
`TOKEN` is an admin token. Run the script with
`kubectl exec -i ... -c app -- python3 - <host> apply < script.py`, so the token never
leaves the pod. The script does this:

1. `POST /api/config/config_entries/flow` with `{"handler": "mqtt", "entry_id": "<id>"}`.
2. Answer the returned `broker` form, also by `POST`. Use every field's
   `suggested_value`, including the `other_settings` section as a dict, and change
   only `broker`. The password's suggested value is a placeholder that keeps the
   stored password.

The result is `abort` / `reconfigure_successful`, and HA reloads the entry.

### One credential, three copies

There is one MQTT user, with the username and password from the 1Password item
**`emqx`** (`X_EMQX_MQTT_USERNAME` / `X_EMQX_MQTT_PASSWORD`). The item keeps its
old name. Each client holds its own copy of the password:

- zigbee2mqtt reads the same `emqx` item, through `zigbee2mqtt-secret`.
- AppDaemon reads `MQTT_USER` / `MQTT_PASSWORD` from its **own** `appdaemon` item.
- Home Assistant keeps the password in its config entry.

So rotating the password means changing all three: both 1Password items, then the HA
reconfigure flow with the new password. Mosquitto picks up the new `passwd` within
the ExternalSecret's 5-minute refresh, and reloader restarts the pod.

## Configuration choices (`mosquitto.conf`)

- `max_packet_size 33554432` (32 MiB). Mosquitto 2.1 lowered the default to
  2,000,000 bytes, but `zigbee2mqtt/bridge/devices` is about 3.7 MB and retained.
  zigbee2mqtt caps what it accepts itself at 10 MiB
  (`ZIGBEE2MQTT_CONFIG_MQTT_MAXIMUM_PACKET_SIZE`, set 2026-09-09). Without that,
  it declares 1 MiB and never receives its own `bridge/devices` echo.
- `max_queued_messages 100000`. In Mosquitto this cap also applies to **QoS 0**
  delivery: once a client has that many packets waiting to be written, more QoS 0
  messages to it are dropped. Home Assistant subscribes to `homeassistant/#` and
  gets every retained discovery config, about 7,400 of them, in one burst. With the
  default of 1000, part of that burst can be dropped, and those entities stay
  unavailable. EMQX's retainer limiter did the same on 2026-09-09.
- `persistence true`, `autosave_interval 60`: a restart keeps the retained store,
  so no zigbee2mqtt restart is needed after a broker bounce.
- `allow_anonymous false` and the `password-file` plugin. There is no ACL, so the
  one user can publish and subscribe to everything, as EMQX's `admin` superuser could.

## Publish or subscribe by hand

The broker container mounts the plaintext `passwd`, so the credential never leaves
the pod:

```bash
kubectl exec -n database mosquitto-0 -c app -- sh -c \
  'U=$(cut -d: -f1 /mosquitto/secret/passwd); P=$(cut -d: -f2- /mosquitto/secret/passwd);
   mosquitto_sub -h 127.0.0.1 -u "$U" -P "$P" -t "zigbee2mqtt/bridge/state" -C 1 -W 5 -v'
```

Swap in `mosquitto_pub ... -t <topic> -m <payload>` to publish. Never echo `$P`.
Useful reads: `$SYS/broker/clients/connected`, `$SYS/broker/retained messages/count`,
`$SYS/broker/uptime`.

## After a broker restart

**What a restart costs Home Assistant.** When the broker goes away, HA marks its
~6,500 MQTT entities unavailable; when it comes back, HA marks them available again
and re-reads ~7,300 retained messages. While that runs, HA's event loop sits at one
full core: about 3 minutes after the 2026-10-06 01:32Z EMQX restart, and 2.5 minutes
after the Mosquitto cutover's reconfigure. While the loop is pegged, the dashboards,
ha-mcp and AppDaemon's websocket fall behind ("Client unable to keep up with pending
messages"), and other integrations can drop and reconnect. So batch broker changes,
and do not pile more work onto HA (MCP calls, AppDaemon restarts, z2m restarts) until
`kubectl top pod -n home-automation -l app.kubernetes.io/name=home-assistant` is
back under ~0.3 core.

Otherwise, usually nothing. The clients reconnect on their own and the retained store comes back
from `mosquitto.db`. Check:

1. `kubectl logs -n database mosquitto-0 -c app | grep -E 'Restored|New client connected'`
   shows the restored retained count, and three clients (zigbee2mqtt `mqttjs_*`,
   Home Assistant, `appdaemon_mqtt_client`) connecting again.
2. HA MQTT entities: count the unavailable ones,
   `{{ integration_entities('mqtt') | select('is_state','unavailable') | list | count }}`.
   About 6 is normal (devices that are really offline). After a reconnect, HA's
   event loop can take about 3 minutes to work through the retained flood. Wait for
   it before you act.
3. Only if the retained store was lost (a fresh PVC) or the entities stay unavailable:
   `kubectl rollout restart deploy/zigbee2mqtt -n home-automation`, so zigbee2mqtt
   republishes discovery and state, then poke the groups. Group state is not retained
   per device, so publish `{"state":""}` to `zigbee2mqtt/<group friendly_name>/get`
   for each group listed in the retained `zigbee2mqtt/bridge/groups`. That is a read,
   not a command, and it switches no lights.

## `MosquittoNotReady`: no Ready broker pod for 2 minutes

```bash
kubectl get pod -n database -l app.kubernetes.io/name=mosquitto -o wide
kubectl describe pod -n database mosquitto-0 | grep -iE 'probe|killing|timeout|oom'
kubectl logs -n database mosquitto-0 -c app --previous | tail -50
kubectl logs -n database mosquitto-0 -c hash-passwords
```

| what you see | cause | fix |
|---|---|---|
| probe timeouts, node load high (`NodeLoadSaturated`) | CPU starvation, as on 2026-10-05 | Fix the node (`cpu-starvation.md`). The broker has a CPU request and TCP-only probes, so it rides out contention. |
| `Init:CrashLoopBackOff`, `hash-passwords` errors | `mosquitto-secret` is missing or empty (ExternalSecret / 1Password) | `kubectl get externalsecret -n database mosquitto`; fix the 1Password item `emqx` |
| `Error: Unable to open config file` / a config parse error | a bad `mosquitto.conf` edit | revert it in git |
| `OOMKilled` | a client's outgoing queue ran away (`max_queued_messages`) | find the client in the log before raising the limit |
| `Pending`, volume attach errors | Ceph / RBD | the Ceph runbooks. Do not delete the PVC to get it scheduled. |

**Do not delete `data-mosquitto-0` to "fix" a restart loop.** It holds the retained
store. Losing it costs a zigbee2mqtt restart plus group pokes, which is recoverable
but not a fix for anything.

## The 2026-10-06 cutover (EMQX → Mosquitto), for next time

The sequence was #3409 (deploy next to EMQX), #3410 (point the clients at `mqtt`,
with the client Kustomizations suspended until a real-credential smoke test passed),
then the HA reconfigure, then a third PR that retired EMQX.

- zigbee2mqtt moved at 02:42:59Z and AppDaemon at 02:43:58Z. HA moved only at
  02:59:02Z.
- In that gap, HA's event loop was pegged for about 11 minutes (02:43-02:54Z), and
  its memory went from 2.7 to 4.6 GiB. Memory was back to 2.7 GiB by 02:56Z. Three
  things stacked:
  - The broker HA was still on lost zigbee2mqtt, so ~6,500 entities went unavailable.
  - AppDaemon restarted and re-subscribed.
  - Two `ha_set_integration` reconfigure calls timed out against the busy loop. Their
    identity check lists the entry's 7,210 entities.
- The stalled loop missed MQTT keepalives, so EMQX dropped HA too.
- Once the loop recovered, the REST fallback above moved HA in 5 s. HA was
  re-subscribed and settled 2.5 minutes later: unavailable entities 6, the same as
  before.

Next time, reconfigure HA immediately after zigbee2mqtt moves, or before it, and do
not retry against a pegged loop.

