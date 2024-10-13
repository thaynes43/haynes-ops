# Ceph Cheat Sheet

Merge of my notes from proxmox and commands from [this guide](https://www.talos.dev/v1.8/kubernetes-guides/configuration/ceph-with-rook/)

## Talos

### Misc TOOD

```bash
kubectl get storageclass

kubectl --namespace rook-ceph get cephclusters.ceph.rook.io rook-ceph

kubectl --namespace rook-ceph get cephclusters.ceph.rook.io

```

Pre-condtion before talos upgrade:

```yaml
kubectl --namespace rook-ceph wait --timeout=1800s --for=jsonpath='{.status.ceph.health}=HEALTH_OK' rook-ceph
```

### Remove from k8s

```bash
kubectl --namespace rook-ceph patch cephcluster rook-ceph --type merge -p '{"spec":{"cleanupPolicy":{"confirmation":"yes-really-destroy-data"}}}'

kubectl delete storageclasses ceph-block ceph-bucket ceph-filesystem

kubectl --namespace rook-ceph delete cephblockpools ceph-blockpool

kubectl --namespace rook-ceph delete cephobjectstore ceph-objectstore

kubectl --namespace rook-ceph delete cephfilesystem ceph-filesystem
```

Now delete cluster:

```bash
kubectl --namespace rook-ceph delete cephcluster rook-ceph

helm --namespace rook-ceph uninstall rook-ceph-cluster
```

Now the operator:
```bash
helm --namespace rook-ceph uninstall rook-ceph
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
spec:
  restartPolicy: Never
  nodeName: <storage-node-name>
  containers:
  - name: disk-wipe
    image: busybox
    securityContext:
      privileged: true
    command: ["/bin/sh", "-c", "dd if=/dev/zero bs=1M count=100 oflag=direct of=<device>"]
EOF
pod/disk-wipe created

$ kubectl wait --timeout=900s --for=jsonpath='{.status.phase}=Succeeded' pod disk-wipe
pod/disk-wipe condition met

$ kubectl delete pod disk-clean
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