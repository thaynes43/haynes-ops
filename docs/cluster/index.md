# Omni

For this cluster I am taking advantage of [omni](https://omni.siderolabs.com/) from Sidero Labs to manage my Talos installation. The home-ops clusters I've reviewed rely heavily on `talosctl` to manage their clusters but I wanted to skip a few steps and for $10/month I figured it was worth a shot. After getting more familiar with Talos and Omni I do think you can live with out it but I am enjoying the auth and VPN access that comes out of the box.

## The Journey Begins

At this point I've gotten started but documenting as I go endes up being a mess so here's the goods:

![initial-omni-dash](docs/images/cluster/initial-omni-dash.png)

I'll recap how the story went so far but first some TODOs:

- Bootstraping flux sets those limits but I'm not sure why

Try:

> **WARNING** Wiping disks is for ceph. They wipe `/dev/nvme#` which happens to change all the time when talos re-images the OS. Make sure these paths are correct before running this!!!

```bash
task omni:sync
task rook:wipe-disks-talosm01
task rook:wipe-disks-talosm02
task rook:wipe-disks-talosm03
task talos:install-helm-apps
task flux:bootstrap
```
> **TODO** See Rook task file and add `RookDiskWipe` in which was needed to get bluestore partition off of OSDs