# ZFS Cheatsheet

## Clean Up

See what is allocating space:

```bash
zfs list -t filesystem,volume
```

Check for snapshots:

```bash
zfs list -t snapshot
```

Delete stuff that is not needed anymore:

```bash
zfs destroy rpool/data/vm-<VMID>-disk-<DISKID>
zfs destroy rpool/data/vm-<VMID>-disk-<DISKID>@<snapshot_name>
```

See what else there is:

```bash
zfs list -o space
```