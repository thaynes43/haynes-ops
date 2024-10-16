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

