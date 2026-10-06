# CPU starvation on a node: `NodeLoadSaturated` and `EMQXCoreNotReady`

Both alerts are `severity: critical`, so they page Pushover, and they carry no
`scope: host`, so the alert-responder diagnoses them. They came out of the same
incident.

**2026-10-05, 23:41-00:12Z.** An agent session in the dev-env pod (namespace `dev`,
which had no CPU limit then) ran about 60 `while :; do :; done` burners plus parallel
vitest runs on talosm02, a node with 20 threads. Load1 passed 100. Every pod on the
node starved, BestEffort pods first. EMQX restart-looped: its 1 s `/status` liveness
probe timed out, and the broker had no Ready pod from 00:04 to 00:12:30Z, so every
Zigbee light was dead. The traefik, authentik, cnpg-operator, cert-manager-webhook,
cilium-operator, k8tz, snapshot-controller, node-exporter and blackbox-exporter health
checks failed on the same node. Nothing paged. The same dev-env pod had already held
talosm02 at 99% CPU for hours on 2026-09-26.

## `NodeLoadSaturated`: what it measures

The node's 15-minute load average is above 2x its CPU count, and has been for 10
minutes. A starved node also starves its own node-exporter, so expect scrape gaps
(`up{job="node-exporter"} == 0`) from that node. The rule bridges them with
`last_over_time(...[15m])`. A gap is part of the symptom, not a separate fault.

Load counts runnable tasks **and** tasks in uninterruptible (D-state) sleep, so first
tell CPU starvation from a storage stall:

```promql
instance:node_cpu_utilisation:rate5m{kubernetes_node="<node>"}   # high (>0.8): CPU. Low: D-state I/O, see the Ceph/NFS runbooks
topk(5, sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{node="<node>", container!=""}[5m])))
```

`kubectl top pod -A --sort-by=cpu` gives the same ranking when the kubelet answers.

## What to do with the consumer

- **dev-env (namespace `dev`).** An agent session is running the load. Kill the
  runaway processes, not the pod: from a shell in the dev-env pod, run
  `ps -eo pid,pcpu,etime,args --sort=-pcpu | head`, then `kill` the offenders.
  Deleting or restarting the dev-env pod ends **every** agent session in it, and
  that is Tom's call. The CPU limit for dev-env is haynes-ops#3381, a held draft
  because merging it restarts the pod. The kubelet `systemReserved`/`kubeReserved`
  follow-up is #3382.
- **Any other pod.** Capture its logs first, then restart it if it is a runaway loop.
  If it is legitimate load, it needs requests and limits through git.
- **The rem-\* lane.** It may diagnose and report on this alert. It may not delete
  or restart the dev-env pod, and it may not delete any PVC. A starved node is not a
  storage fault. When the culprit is dev-env, escalate with the process list.

## `EMQXCoreNotReady`: no Ready broker pod for 2 minutes

Read the pod's state before acting:

```bash
kubectl get pod -n database -l apps.emqx.io/db-role=core -o wide
kubectl describe pod -n database <pod> | grep -iE 'probe|killing|timeout'
kubectl logs -n database <pod> -c emqx --previous | tail -50
```

| what you see | cause | fix |
|---|---|---|
| one pod, `Liveness probe failed ... timeout`, node load high | starvation (2026-10-05) | Fix the node (above). The broker recovers on its own with the same PVC and no data loss. zigbee2mqtt, Home Assistant and AppDaemon reconnect on their own (checked with `emqx ctl clients list` after 2026-10-05). |
| two `emqx-core-<hash>` pods, or `SINGLE_NODE_LICENSE` in the log | a pod-template change started an operator blue-green | follow `emqx-config-drift.md` → *Pod-template change procedure*: scale the old StatefulSet to 0, then the fresh-PVC step |
| `mria_mnesia: still waiting for table(s)` and the old core pod is gone | aftermath of a blue-green | the fresh-PVC recovery in `emqx-config-drift.md`. Only this case justifies deleting the broker PVC. |

**Never delete the broker PVC for a starvation restart loop.** The PVC holds the
retained store (about 7,450 messages, including Home Assistant's discovery configs),
the Mnesia users and `cluster.hocon`.
