# Sidero Labs Cheat Sheet

Useful Omnictl & Talosctl and commands geared to managing a cluster with Omni.

## Omni

Quick check:

```yaml
omnictl cluster status haynes-ops
```

## Talos

Quick check on the cluster and config:

```bash
talosctl get members
```

```bash
talosctl disks
talosctl disks -n <iNODE_IPp>
```

For partitions: 

```bash
talosctl get blockdevices
talosctl -n 192.168.40.59 get blockdevices
```

For id:

```bash
talosctl -n <NODE_IP> ls /dev/disk/by-id
```

### Not disks

```bash
talosctl get links
talosctl get links -n <NODE_IP> --insecure
talosctl get links -o yaml
```

```bash
talosctl get addresses
talosctl -n <NODE_IP> get addresses
talosctl get address eth0/172.20.0.2/24 -o yaml
```

```bash
talosctl get addressspecs
talosctl -n <NODE_IP> get addressspecs
talosctl get addressspecs eth0/172.20.0.2/24 -o yaml
```

```bash
talosctl get hostnamestatus
talosctl -n <NODE_IP>  get hostnamestatus
talosctl get hostnamespec -o yaml --namespace network-config
```