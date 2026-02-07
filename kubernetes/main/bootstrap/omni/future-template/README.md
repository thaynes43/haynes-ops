# Future cluster template (example) + patch layout

This folder is **intentionally not used by production**. It contains:

- `cluster-template-new-network-config.yaml`: an **example Omni cluster template** showing how to externalize per-machine networking into file-based patches.
- `patches/`: the patch files referenced by the example template.

Reference: [Omni cluster template schema](https://docs.siderolabs.com/omni/reference/cluster-templates).

## Why this exists

When nodes are multi-homed (mgmt + VLAN NICs), we want deterministic default-route selection during upgrades. The production template currently embeds the NIC config inline, but this example shows the “best practice” modular approach:

- keep “everything except networking” in each machine’s inline patch
- move `machine.network` (hostname, interfaces, DHCP route metrics, nameservers, etc.) into a file patch per machine

## How file patches are referenced

Omni supports referencing patch files from `Cluster`, `ControlPlane`, `Workers`, or per-`Machine` documents.

In the example template, we attach a second patch entry in each `kind: Machine` doc:

```yaml
kind: Machine
name: c1f22c00-4390-11ef-a299-436f6535c900
patches:
  - idOverride: 400-cm-c1f22c00-4390-11ef-a299-436f6535c900
    inline:
      machine:
        # ... everything except machine.network ...
  - idOverride: 910-network-talosm02
    file: kubernetes/main/bootstrap/omni/future-template/patches/network/talosm02.yaml
```

Notes:
- `file:` paths are **relative to the directory you run `omnictl` from**.
  - The example uses repo-root-relative paths assuming you run `omnictl` from the repo root.
- `idOverride` is optional, but recommended so patch IDs don’t change if you rename/move files.

## What would be removed from production `cluster-template.yaml` (when you migrate)

For each `kind: Machine` inline patch, you would remove these keys (since they’d live in the file patch):

- `machine.network.hostname`
- `machine.network.interfaces`
- `machine.network.nameservers`
- `machine.network.disableSearchDomain`

Everything else stays inline (install options, kernel modules, sysctls, kubelet config, files, node labels, etc.).

