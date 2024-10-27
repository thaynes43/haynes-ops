# Ceph Cheat Sheet

Merge of my notes from proxmox and commands from [this guide](https://www.talos.dev/v1.8/kubernetes-guides/configuration/ceph-with-rook/)

## Rook

Restart operator to re-invoke cluster init:

```bash
kubectl -n rook-ceph delete pod -l app=rook-ceph-operator
```

Get into a place you can use the regular commands:

```bash
kubectl --namespace rook-ceph exec -it deploy/rook-ceph-operator -- bash
```

> **TODO** Move this

```bash
kubectl --namespace rook-ceph exec -it deploy/rook-ceph-operator -- bash
rook multus validation run --public-network=network/multus-public --cluster-network=network/multus-ceph -n rook-ceph
```
This leaves a MESS so you need to run:

```bash
rook multus validation cleanup --namespace rook-ceph
```

Then the next validation:

```bash
rook multus validation config converged
```

``` bash
Example:

thaynes@HaynesHyperion:~$ kubectl --namespace rook-ceph exec -it deploy/rook-ceph-operator -- bash
Defaulted container "rook-ceph-operator" out of: rook-ceph-operator, k8tz (init)
[rook@rook-ceph-operator-69745fc466-pc95r /]$ rook multus validation run --help
2024/10/26 10:59:57 maxprocs: Leaving GOMAXPROCS=20: CPU quota undefined

Run a validation test that determines whether the current Multus and system
configurations will support Rook with Multus.

This should be run BEFORE Rook is installed.

This is a fairly long-running test. It starts up a web server and many
clients to verify that Multus network communication works properly.

It does *not* perform any load testing. Networks that cannot support high
volumes of Ceph traffic may still encounter runtime issues. This may be
particularly noticeable with high I/O load or during OSD rebalancing
(see: https://docs.ceph.com/en/latest/architecture/#rebalancing).
For example, during Rook or Ceph cluster upgrade.

Override the kube config file location by setting the KUBECONFIG environment variable.

Usage:
  rook multus validation run [--public-network=<nad-name>] [--cluster-network=<nad-name>] [flags]

Flags:
      --cluster-network string                   The name of the Network Attachment Definition (NAD) that will be used for Ceph's cluster network. This should be a namespaced name in the form <namespace>/<name> if the NAD is defined in a different namespace from the cluster namespace.
  -c, --config string                            The validation test config file to use. This cannot be used with other flags except --host-check-only.
      --daemons-per-node int                     The number of validation test daemons to run per node. It is recommended to set this to the maximum number of Ceph daemons that can run on any node in the worst case of node failure(s). The default value is set to the worst-case value for a Rook Ceph cluster with 3 portable OSDs, 3 portable monitors, and where all optional child resources have been created with 1 daemon such that they all might run on a single node in a failure scenario. If you aren't sure what to choose for this value, add 1 for each additional OSD beyond 3. (default 19)
      --flaky-threshold-seconds timeoutSeconds   This is the time window in which validation clients are all expected to become 'Ready' together. Validation clients are all started at approximately the same time, and they should all stabilize at approximately the same time. Once the first validation client becomes 'Ready', the tool checks that all of the remaining clients become 'Ready' before this threshold duration elapses. In networks that have connectivity issues, limited bandwidth, or high latency, clients will contend for network traffic with each other, causing some clients to randomly fail and become 'Ready' later than others. These randomly-failing clients are considered 'flaky.' Adjust this value to reflect expectations for the underlying network. For fast and reliable networks, this can be set to a smaller value. For networks that are intended to be slow, this can be set to a larger value. Additionally, for very large Kubernetes clusters, it may take longer for all clients to start, and it therefore may take longer for all clients to become 'Ready'; in that case, this value can be set slightly higher. (default 30s)
  -h, --help                                     help for run
      --host-check-only                          Only check that hosts can connect to the server via the public network. Do not start clients. This mode is recommended when a Rook cluster is already running and consuming the public network specified.
  -n, --namespace string                         The namespace for validation test resources. It is recommended to set this to the namespace in which Rook's Ceph cluster will be installed. (default "rook-ceph")
      --nginx-image string                       The Nginx image used for the validation server and clients. (default "quay.io/nginx/nginx-unprivileged:stable-alpine")
      --public-network string                    The name of the Network Attachment Definition (NAD) that will be used for Ceph's public network. This should be a namespaced name in the form <namespace>/<name> if the NAD is defined in a different namespace from the cluster namespace.
      --service-account string                   The name of the service account that will be used for test resources. (default "rook-ceph-system")
      --timeout-minutes timeoutMinutes           The time to wait for resources to change to the expected state. For example, for the test web server to start, for test clients to become ready, or for test resources to be deleted. At longest, this may need to reflect the time it takes for client pods to to pull images, get address assignments, and then for each client to determine that its network connection is stable. Minimum: 1 minute. Recommended: 2 minutes or more. (default 3m0s)

Global Flags:
      --log-level string   logging level for logging/tracing output (valid values: ERROR,WARNING,INFO,DEBUG) (default "INFO")
[rook@rook-ceph-operator-69745fc466-pc95r /]$ rook multus validation config --help
2024/10/26 11:00:16 maxprocs: Leaving GOMAXPROCS=20: CPU quota undefined
Generate a validation test config file for different default scenarios to stdout.

Usage:
  rook multus validation config [command]

Available Commands:
  converged               Example config for a cluster that runs storage and user workloads on all nodes.
  dedicated-storage-nodes Example config file for a cluster that uses dedicated storage nodes.
  stretch-cluster         Example config file for a stretch cluster with dedicated storage nodes.

Flags:
  -h, --help   help for config

Global Flags:
      --log-level string   logging level for logging/tracing output (valid values: ERROR,WARNING,INFO,DEBUG) (default "INFO")

Use "rook multus validation config [command] --help" for more information about a command.
```

## Talos

### Status

We can use a tools pod to run `ceph` commands

#### Check Status

```bash
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status
```

#### Clear Warn:

```bash
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph status
```

### Misc TOOD

```bash
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=0
kubectl -n rook-ceph scale deployment rook-ceph-operator --replicas=1
```

```bash
kubectl get storageclass

kubectl -n rook-ceph get cephclusters rook-ceph

kubectl -n rook-ceph get cephclusters
```

Pre-condtion before talos upgrade:

```yaml
kubectl -n rook-ceph wait --timeout=1800s --for=jsonpath='{.status.ceph.health}=HEALTH_OK' rook-ceph
```

### Remove from k8s

```bash
kubectl -n rook-ceph patch cephcluster rook-ceph --type merge -p '{"spec":{"cleanupPolicy":{"confirmation":"yes-really-destroy-data"}}}'

kubectl delete storageclasses ceph-block ceph-bucket ceph-filesystem

kubectl -n rook-ceph delete cephblockpools ceph-blockpool

kubectl -n rook-ceph delete cephobjectstore ceph-objectstore

kubectl -n rook-ceph delete cephfilesystem ceph-filesystem
```

Now delete cluster:

```bash
kubectl -n rook-ceph delete cephcluster rook-ceph

helm -n rook-ceph uninstall rook-ceph-cluster
```

Now the operator:
```bash
helm -n rook-ceph uninstall rook-ceph
```

### Cluseter Finalizer

```bash
kubectl patch cephcluster rook-ceph -n rook-ceph --type=json -p='[{"op": "remove", "path": "/metadata/finalizers"}]'
```

```bash
for CRD in $(kubectl get crd -n rook-ceph | awk '/ceph.rook.io/ {print $1}'); do
    kubectl get -n rook-ceph "$CRD" -o name | \
    xargs -I {} kubectl patch -n rook-ceph {} --type merge -p '{"metadata":{"finalizers": []}}'
done
```

```bash
kubectl -n rook-ceph patch configmap rook-ceph-mon-endpoints --type merge -p '{"metadata":{"finalizers": []}}'
kubectl -n rook-ceph patch secrets rook-ceph-mon --type merge -p '{"metadata":{"finalizers": []}}'
```

### Finish off metadata

Talos may need some massaging:

```bash
$ cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: disk-clean
spec:
  restartPolicy: Never
  nodeName: <storage-node-name>
  volumes:
  - name: rook-data-dir
    hostPath:
      path: <dataDirHostPath>
  containers:
  - name: disk-clean
    image: busybox
    securityContext:
      privileged: true
    volumeMounts:
    - name: rook-data-dir
      mountPath: /node/rook-data
    command: ["/bin/sh", "-c", "rm -rf /node/rook-data/*"]
EOF
pod/disk-clean created

$ kubectl wait --timeout=900s --for=jsonpath='{.status.phase}=Succeeded' pod disk-clean
pod/disk-clean condition met

$ kubectl delete pod disk-clean
pod "disk-clean" deleted
```

And wipe the disks:

```yaml
$ cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: disk-wipe
  namespace: rook-ceph
spec:
  restartPolicy: Never
  nodeName: talosm02
  containers:
  - name: disk-wipe
    image: busybox
    securityContext:
      privileged: true
    command: ["/bin/sh", "-c", "dd if=/dev/zero bs=1M count=100 oflag=direct of=/dev/nvme1n1"]
EOF
pod/disk-wipe created

$ kubectl wait --timeout=900s --for=jsonpath='{.status.phase}=Succeeded' pod disk-wipe
pod/disk-wipe condition met

$ kubectl delete pod disk-wipe
pod "disk-wipe" deleted
```

## Ceph

Not sure how these would work unless you install ceph and a config that points to the cluster on another machine (seeing you can't run ceph cli on talos)

### Archive crash warnings

These happen when I reboot.

```bash
ceph crash archive-all
```

### Cluster info

#### Status

Overall status of the cluster.

```bash
ceph status || ceph -w
```

#### Config

```bash
ceph config dump
```
#### Monitors

Get details about the monitors.

```bash
ceph mon dump
```

### Ceph Services

#### See Services

```bash
ceph mgr services
```

#### Restart Service

```bash
ceph mgr module disable dashboard
ceph mgr module enable dashboard
```