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

### edgem02

eth0: `58:47:ca:77:0d:aa`

### edgew01

Machine ID: `77d65c00-5811-11ef-b65b-a8751caa6100`