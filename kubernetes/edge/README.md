# Edge Cluster

The edge cluster uses self hosted `omni` from the main cluster to control the nodes. It's intended to be used for development and testing and no services hosted here shall be used in "production".

## Managing Config Context

We need to change context for kube, talos, and omni configs. 

### Omnictl

The omnictl config may be found at `/home/thaynes/.config/omni/config`.

`omnictl config contexts` will list all contexts and * the current.

To switch:

```bash
omnictl config context haynes-edge
omnictl config context haynes-ops
```

Cheat sheet:

```bash
omnictl get clusters
omnictl get machines
```

Then test and apply templates:

```bash
cd /home/thaynes/haynes-ops/kubernetes/edge/bootstrap/omni

omnictl cluster template validate -f haynes-edge-cluster-template.yaml

omnictl cluster template sync -f haynes-edge-cluster-template.yaml --verbose
omnictl cluster template status -f haynes-edge-cluster-template.yaml
```

### Talosctl

Talos config may be found at `/home/thaynes/.talos/config`.

`talosctl config contexts` will list all contexts and * the current.

To switch:

```bash
talosctl config context haynes-edge
talosctl config context haynes-ops
```

Cheat sheet:

- To list all resources `talosctl get rd`
- `talosctl memory`

### Kubectl

Kube config may be found at `/home/thaynes/.kube`.

`kubectl config get-contexts` will list all contexts and * the current. 

To switch:

```bash
kubectl config use-context haynes-edge
kubectl config use-context haynes-ops
```

Chreat sheet:

- `kubectl config current-context`

## Nodes

Extensions:

```yaml
  - siderolabs/nut-client
  - siderolabs/thunderbolt

  # Only for intel nodes
  - siderolabs/intel-ucode
  - siderolabs/i915

  # Only for nvidia GPU
  - siderolabs/nvidia-container-toolkit-lts
  - siderolabs/nvidia-open-gpu-kernel-modules-lts
```

Kernel Args:

```yaml
          extraKernelArgs:
            - nomodeset      # Only for iGPU
            - net.ifnames=0
```

### edgem01

__from pve01__

eth0: `58:47:ca:77:13:da`

### edgem02

__from pve02__

eth0: `58:47:ca:77:0d:aa`

### edgem03

__from pve03__

eth0: `58:47:ca:77:0a:7a`

### edgew01

Machine ID: `77d65c00-5811-11ef-b65b-a8751caa6100`

## Notes on migrating

Remove proxmox nodes

Part 1 - Ceph

- Delete mgr, mon, mds
- Set OSD to out
- Let cluster rebalance
- Set OSD to down
- Verify there are no undersized pgs
- Delete OSDs
- `ceph osd crush rm NODE`

Part 2 - HA cluster

- `pvecm delnode NODE`
- pvecm nodes

# Getting Edge Cluster Online

## TODOs

- [ ] fix KUBERNETES_DIR as it assumes a single cluster.

## Install Prerequisites 

`install-helm-apps` task unwrapped:

```sh
HELMFILE_FILE: "{{.KUBERNETES_DIR}}/bootstrap/helmfile.yaml" # ./kubernetes/edge/bootstrap/helmfile.yaml
KUBERNETES_DIR: "{{.ROOT_DIR}}/kubernetes"
helmfile --kubeconfig {{.KUBECONFIG_FILE}} --file {{.HELMFILE_FILE}} apply --skip-diff-on-install --suppress-diff
```

## TODO Rook-Ceph Wipe

Gotta wipe the NVMes rook will use later

## Bootstrap Flux

Need `CLUSTER_SECRET_SOPS_FILE: "{{.KUBERNETES_DIR}}/flux/vars/cluster-secrets.sops.yaml"`

Settings:

```yaml
CLUSTER_SETTINGS_FILE: "{{.KUBERNETES_DIR}}/flux/vars/cluster-settings.yaml"
KUBECONFIG_FILE: "{{.ROOT_DIR}}/kubeconfig"
CLUSTER_SECRET_SOPS_FILE: "{{.KUBERNETES_DIR}}/flux/vars/cluster-secrets.sops.yaml"
AGE_FILE: "{{.ROOT_DIR}}/age.key"
```

`flux:bootstrap` unwrapped: 

```yaml
  bootstrap:
    desc: Bootstrap Flux into a Kubernetes cluster
    cmds:
      - kubectl apply --kubeconfig {{.KUBECONFIG_FILE}} --server-side --kustomize {{.KUBERNETES_DIR}}/bootstrap/flux
      - |
        if ! kubectl --kubeconfig {{.KUBECONFIG_FILE}} -n flux-system get secret sops-age >/dev/null 2>&1; then
          cat {{.AGE_FILE}} | kubectl --kubeconfig {{.KUBECONFIG_FILE}} -n flux-system create secret generic sops-age --from-file=age.agekey=/dev/stdin
        else
          echo "sops-age secret already exists, skipping creation."
        fi
      - sops --decrypt {{.CLUSTER_SECRET_SOPS_FILE}} | kubectl apply --kubeconfig {{.KUBECONFIG_FILE}} --server-side --filename -
      - kubectl apply --kubeconfig {{.KUBECONFIG_FILE}} --server-side --filename {{.CLUSTER_SETTINGS_FILE}}
      - kubectl apply --kubeconfig {{.KUBECONFIG_FILE}} --server-side --kustomize {{.KUBERNETES_DIR}}/flux/config
    preconditions:
      - msg: Missing kubeconfig
        sh: test -f {{.KUBECONFIG_FILE}}
      - msg: Missing Sops Age key file
        sh: test -f {{.AGE_FILE}}
```