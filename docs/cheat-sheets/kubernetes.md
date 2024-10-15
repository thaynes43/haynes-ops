# Kubernetes Cheat Sheet

Find api resources for namespace

```bash
kubectl api-resources --verbs=list --namespaced -o name \
  | xargs -n 1 kubectl get --show-kind --ignore-not-found -n <namespace>
```

## Flux

```bash
kubectl logs -n flux-system deploy/helm-controller
```