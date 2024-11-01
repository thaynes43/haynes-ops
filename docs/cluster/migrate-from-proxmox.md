# Migrating a node from Proxmox to Talos

Goal for the new stack is [Omni](https://haynes.omni.siderolabs.io/omni) controlled bare metal Talos stack.

https://www.talos.dev/v1.7/talos-guides/install/omni/

## Reclaiming PVE node in k3s cluster

### Removing VM from Cluster

```bash
kubectl get nodes
thaynes@HaynesHyperion:~$ kubectl get nodes
NAME      STATUS   ROLES                       AGE   VERSION
kubem01   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubem02   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubem03   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubew01   Ready    <none>                      65d   v1.30.3+k3s1
kubew02   Ready    <none>                      65d   v1.30.3+k3s1
kubew03   Ready    <none>                      65d   v1.30.3+k3s1
kubew04   Ready    <none>                      65d   v1.30.3+k3s1
```

Find the node you want and drain it with:

```bash
kubectl drain kubew04 --ignore-daemonsets --delete-local-data
```

Then just delete it:

```bash
kubectl delete node kubew04
```

And now it's gone!

```bash
thaynes@HaynesHyperion:~$ kubectl get nodes
NAME      STATUS   ROLES                       AGE   VERSION
kubem01   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubem02   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubem03   Ready    control-plane,etcd,master   65d   v1.30.3+k3s1
kubew01   Ready    <none>                      65d   v1.30.3+k3s1
kubew02   Ready    <none>                      65d   v1.30.3+k3s1
kubew03   Ready    <none>                      65d   v1.30.3+k3s1
```

### Reclaim Nodes from PVE

Now that the k3s isn't relying on the node we can shut down or delete that VM. Then remove this node from the HA cluster and migrate all HA VMs off to other nodes.

> **NOTE** I also have a load balancer for the proxmox UI so I'll clean up that config to remove this

#### Ceph

1. Set OSDs to "out" and wait for Ceph to rebalance
1. Destroy MGR, MON, and MDS from the UI
1. Once OSDs are empty destroy them

#### PVE

Once nothing is running on the node we can follow [these steps](https://forum.proxmox.com/threads/pve-remove-one-node-from-ceph-cluster.122456/) to remove nodes from proxmox. 

Delete the node with:

```bash
pvecm delnode <NODE>
```

Then clean it out here:

```bash
root@pve01:/etc/pve/nodes# cd /etc/pve/nodes
root@pve01:/etc/pve/nodes# rm -R pve05/
```

You can also clean the node itself out by deleting:

```bash
systemctl stop pve-cluster corosync
pmxcfs -l
rm /etc/corosync/*
rm /etc/pve/corosync.conf
killall pmxcfs
systemctl start pve-cluster
```

And `rm -R /etc/pve/nodes` but I didn't get that far.

## Update MS-01 BIOS

While I'm at it there's a BIOS update to apply.

First upgrade BIOS for MS-01.
- [tutorial](https://www.virtualizationhowto.com/2024/09/how-to-upgrade-the-minisforum-ms-01-bios/) 
- [download](https://www.minisforum.com/support/?lang=en#/support/page/download/108)

## Clean Drives

Boot using Gparted and delete any data on the drives we will be using. This is especially important for Ceph as that is picky and hard to fix up the drives from Talos. 

Once you add the node to the cluster, before configurign Ceph, run this to wipe the partition table:

```yaml
$ cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: disk-wipe
  namespace: rook-ceph
spec:
  restartPolicy: Never
  nodeName: talosm01
  containers:
  - name: disk-wipe
    image: busybox
    securityContext:
      privileged: true
    command: ["/bin/sh", "-c", "dd if=/dev/zero bs=1M count=100 oflag=direct of=/dev/nvme0n1"]
EOF
pod/disk-wipe created

$ kubectl wait --timeout=900s --for=jsonpath='{.status.phase}=Succeeded' pod disk-wipe
pod/disk-wipe condition met

$ kubectl delete pod disk-wipe
pod "disk-wipe" deleted
```