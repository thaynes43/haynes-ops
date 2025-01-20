cephfilesystemsubvolumegroups.ceph.rook.io                 2024-10-31T20:43:22Z
volumegroupsnapshotclasses.groupsnapshot.storage.k8s.io    2025-01-19T02:24:00Z
volumegroupsnapshotcontents.groupsnapshot.storage.k8s.io   2024-10-31T20:43:01Z
volumegroupsnapshots.groupsnapshot.storage.k8s.io          2024-10-31T20:43:01Z

See [this thread](https://github.com/kubernetes-csi/external-snapshotter/pull/1150#issuecomment-2557947586):

```bash
kubectl patch crd volumegroupsnapshots.groupsnapshot.storage.k8s.io --subresource='status' --type='merge' -p '{"status":{"storedVersions": ["v1beta1"]}}'
```