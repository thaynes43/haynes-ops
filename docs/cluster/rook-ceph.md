# Rook Ceph on Omni

## TODO

Check out command [here](https://github.com/onedr0p/home-ops/commit/8b0af335d9407dfc1e1ddb762b2026a8e3365679#diff-2310e54712275905c8b328fc318d6c38ff5a6bd81e64454db134519ef27312bfR27) that uses MODEL to look up disk instead of /dev/nvme which has been changing around for this cluster.

## WTF

!!! tip Dependencies 

    - External Secrets (for dashboard)

A cluster insn't a cluster with out some ceph action and that's where this [rook guide](https://www.talos.dev/v1.8/kubernetes-guides/configuration/ceph-with-rook/) on the talos docs comes in. 

??? question "What does this do?"

    This seems cool but we will come back to it later.

    === "One thing"

        This is one thing to check out.

    === "A second thing"

        This is a second thing to check out.

Back to ceph! 

## fstrim

See Bernd-home-ops, has fstrim service runing

```
fstrim is a command-line utility in Linux used to discard (or "trim") unused blocks on a mounted filesystem. When files are deleted or moved, the filesystem updates its metadata to mark those blocks as free. However, the underlying storage device may not be aware that these blocks are no longer in use. Running fstrim informs the storage device about the unused blocks, allowing it to manage its storage space more efficiently.
```