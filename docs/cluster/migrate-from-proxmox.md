---
title: Bare Metal K8S
permalink: /docs/moving-day/bare-metal/
---

Goal for the new stack is [Omni](https://haynes.omni.siderolabs.io/omni) controlled bare metal Talos stack.

https://www.talos.dev/v1.7/talos-guides/install/omni/

## Reclaim Nodes from PVE

Follow [these steps](https://forum.proxmox.com/threads/pve-remove-one-node-from-ceph-cluster.122456/) to remove nodes from proxmox. 

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

And maybe even `rm -R /etc/pve/nodes` but I didn't get that far.

## Update MS-01 BIOS

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