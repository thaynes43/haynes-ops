# Improvement Plan for haynes-ops

This document outlines a plan to modernize `haynes-ops` by adopting best practices from `onedr0p-home-ops`, focusing on stability, maintainability, and feature parity. It includes high-level goals and detailed step-by-step implementation guides.

## 1. Network Modernization: Ingress to Gateway API

**Goal**: Replace standard Ingress resources with the Kubernetes Gateway API to enable advanced routing and automated discovery for monitoring.

### Overview
*   **Current State**: `haynes-ops` uses Traefik with standard `Ingress` resources.
*   **Target State**: Adopt Gateway API using `HTTPRoute` resources.
*   **Why**:
    *   Unlocks `auto-httproute` discovery for Gatus (see Monitoring section).
    *   Standardized configuration via `app-template`'s `route` block.
    *   More powerful traffic routing capabilities.
*   **Risk**: **High**. Changing the ingress layer can cause downtime.

### Detailed Implementation Plan

#### Phase A: Enable Gateway API in Traefik (Edge Cluster First)

*Prerequisite: Traefik must be running on the Edge cluster to test this safely. Add a task to deploy a minimal Traefik instance to Edge if not present.*

1.  **Verify Gateway API CRDs**:
    Ensure the Gateway API CRDs are present on the cluster.
    ```bash
    kubectl get crd gateways.gateway.networking.k8s.io
    ```
    *Note: If missing, they must be installed via a standard installation method (e.g., bundled with a controller chart or a separate HelmRelease) before proceeding.*

2.  **Update Traefik HelmRelease**:
    Modify `kubernetes/main/apps/network/traefik/traefik-internal/app/helmrelease.yaml` to enable the Gateway provider.

    ```yaml
    # ... inside values
    providers:
      kubernetesGateway:
        enabled: true
        experimentalChannel: false
    ```

3.  **Create a GatewayClass and Gateway**:
    Create `kubernetes/main/apps/network/traefik/gateway/gateway.yaml`:

    ```yaml
    apiVersion: gateway.networking.k8s.io/v1
    kind: GatewayClass
    metadata:
      name: traefik
    spec:
      controllerName: traefik.io/gateway-controller
    ---
    apiVersion: gateway.networking.k8s.io/v1
    kind: Gateway
    metadata:
      name: external
      namespace: network
    spec:
      gatewayClassName: traefik
      listeners:
        - name: web
          port: 80
          protocol: HTTP
          allowedRoutes:
            namespaces:
              from: All
        - name: websecure
          port: 443
          protocol: HTTPS
          allowedRoutes:
            namespaces:
              from: All
    ```

#### Phase B: Migrate an App
Update an app's `HelmRelease` to use `route` instead of `ingress` (e.g., starting with `podinfo` or `whoami`).

*   **From (`haynes-ops` Ingress)**:
    ```yaml
    ingress:
      app:
        className: traefik-external
        hosts:
          - host: app.example.com
            paths:
              - path: /
                service:
                  identifier: app
                  port: http
    ```

*   **To (`onedr0p` Route)**:
    ```yaml
    route:
      app:
        parentRefs:
          - name: external
            namespace: network
        hostnames:
          - app.example.com
        rules:
          - backendRefs:
              - name: app
                port: 80
    ```

## 2. Flux Modernization: OCI & Global Patches

**Goal**: Improve performance with OCI Helm charts and reduce boilerplate code using Global Patches.

### Overview
*   **OCI Artifacts**: Switch from `HelmRepository` to `OCIRepository` for faster, more reliable artifact delivery.
*   **Global Patches**: Use Kustomize patches in the root `apps.yaml` to enforce defaults (like `decryption: provider: sops`) across all apps, removing the need for repetitive `ks.yaml` files.
*   **Flux Structure**: Align with `onedr0p`'s optimized GitOps Toolkit structure.

### Detailed Implementation Plan

#### 2.1. Switch to OCI Repositories
`onedr0p` defines charts as OCI artifacts.

1.  **Define OCI Repository**:
    In `kubernetes/shared/repositories/oci-repositories.yaml`:
    ```yaml
    apiVersion: source.toolkit.fluxcd.io/v1beta2
    kind: OCIRepository
    metadata:
      name: bjw-s-charts
      namespace: flux-system
    spec:
      interval: 1h
      url: oci://ghcr.io/bjw-s/helm-charts
      ref:
        tag: latest
    ```

2.  **Update HelmReleases**:
    Change `sourceRef` in your apps.
    ```yaml
    # Old
    chart:
      spec:
        chart: app-template
        sourceRef:
          kind: HelmRepository
          name: bjw-s
    
    # New
    chart:
      spec:
        chart: app-template
        sourceRef:
          kind: OCIRepository
          name: bjw-s-charts
    ```

#### 2.2. Reduce `ks.yaml` Boilerplate (Global Patches)
Instead of defining `decryption: provider: sops` in every single `ks.yaml`, inject it globally.

1.  **Edit `kubernetes/main/flux/apps.yaml`**:
    Add a patch to the root `cluster-apps` Kustomization.

    ```yaml
    patches:
      - patch: |-
          apiVersion: kustomize.toolkit.fluxcd.io/v1
          kind: Kustomization
          metadata:
            name: not-used
          spec:
            decryption:
              provider: sops
              secretRef:
                name: sops-age
        target:
          kind: Kustomization
          group: kustomize.toolkit.fluxcd.io
    ```

### 2.3. Flux Kustomization Namespace Migration
**Goal**: Move app `Kustomization` resources out of `flux-system` and into a dedicated namespace (e.g., `flux-apps` or `flux-kustomizations`) to improve organization and RBAC separation.

*   **Why**: `onedr0p` and others do this to avoid polluting the system namespace.
*   **Challenge**: `flux-system` is special; moving things out requires explicit RBAC for the Kustomize controller.
*   **Plan**:
    1.  Create Namespace `flux-apps`.
    2.  Update `kubernetes/main/flux/apps.yaml` (the root) to target this namespace for child Kustomizations.
    3.  **Crucial**: Ensure `kustomize-controller` service account has permissions to manage resources in target namespaces (e.g., `home-automation`, `media`) *from* the source namespace `flux-apps`. This is usually handled by ClusterRoles, but explicit RoleBindings may be needed if strict isolation is used.

### 2.4. "Flux Operator" Strategy
**Decision**: Stick with the standard **GitOps Toolkit (GOTK)** components (`source-controller`, `kustomize-controller`, etc.) rather than switching to the "Flux Operator" (control-plane-less CRD manager).
*   **Reasoning**: `onedr0p` uses standard GOTK components. The "Flux Operator" is a different architectural pattern often used for managing Flux itself as a resource. Adopting `onedr0p`'s structure (OCI, Global Patches, Components) achieves the desired modernization without re-architecting the control plane.

## 3. Monitoring & Health Checks

**Goal**: Automate health check discovery using Gatus sidecars and HTTPRoutes.

### Overview
*   **Gatus Sidecar**: Use the sidecar pattern to automatically discover `HTTPRoute` resources.
*   **Probes**: Ensure all `HelmReleases` define `liveness`, `readiness`, and `startup` probes.
*   **Alerting**: Configure Alertmanager to route critical alerts to Pushover.

### Detailed Implementation Plan

#### 3.1. Update Gatus HelmRelease
Enable the sidecar pattern used by `onedr0p`.

1.  **Modify `kubernetes/main/apps/observability/gatus/app/helmrelease.yaml`**:
    Add the sidecar container.
    ```yaml
    initContainers:
      gatus-sidecar:
        image:
          repository: ghcr.io/home-operations/gatus-sidecar
          tag: v0.0.11
        args:
          - --auto-httproute # Critical: watches HTTPRoutes
    ```

2.  **RBAC**: Ensure the Gatus service account has permission to `list` and `watch` `HTTPRoutes` and `Gateways`.

#### 3.2. Annotate Apps
Once using `HTTPRoute`, add the annotation to enable monitoring:
```yaml
route:
  app:
    annotations:
      gatus.home-operations.com/enabled: "true"
```

## 5. Templates vs. Components

**Goal**: Migrate from using Kustomize *Templates* (copied files) to *Components* (reusable overlays) to DRY (Don't Repeat Yourself) up the codebase.

### Overview
*   **Current State**: `haynes-ops` copies `ks.yaml` and other resource manifests into every app directory.
*   **Target State**: Use **Kustomize Components** for shared patterns like VolSync replication, Gatus configuration, and common alerts.
*   **Why**:
    *   Updates to a pattern (e.g., changing VolSync schedule) only need to happen in one place (`kubernetes/shared/components`) instead of every single app.
    *   Cleaner app directories.

### Detailed Implementation Plan

#### 5.1. Create Shared Components Directory
Create a structure in `kubernetes/shared/components`:
```text
kubernetes/shared/components/
├── common/
├── gatus/
│   ├── guarded/
│   │   ├── kustomization.yaml
│   │   └── route-patch.yaml
│   └── external/
│       ├── kustomization.yaml
│       └── route-patch.yaml
└── volsync/
    ├── r2/
    │   ├── kustomization.yaml
    │   ├── replication-source.yaml
    │   └── replication-destination.yaml
    └── b2/ ...
```

#### 5.2. Define a Component (Example: Gatus Guarded)
In `kubernetes/shared/components/gatus/guarded/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1alpha1
kind: Component
patches:
  - path: route-patch.yaml
    target:
      kind: HTTPRoute
```

In `kubernetes/shared/components/gatus/guarded/route-patch.yaml`:
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: not-used
  annotations:
    gatus.home-operations.com/enabled: "true"
    gatus.home-operations.com/path: /
```

#### 5.3. Consume Component in App
Update an app's `kustomization.yaml` (e.g., `plex`) to use the component instead of defining raw resources.

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./helmrelease.yaml
components:
  - ../../../../../shared/components/gatus/guarded
```

## 6. Taskfile Cleanup

**Goal**: Simplify local development workflow by removing complex, unused tasks.

### Overview
`haynes-ops` Taskfiles are currently overly complex. We will strip them down to essential commands for `cluster`, `flux`, and `sops`.

### Detailed Implementation Plan

#### 4.1. Simplify `Taskfile.yaml`
Remove unused includes. Keep it flat if possible.

**Proposed Structure**:
```yaml
version: "3"
includes:
  kubernetes: .taskfiles/Kubernetes/Taskfile.yaml
  flux: .taskfiles/Flux/Taskfile.yaml
  sops: .taskfiles/Sops/Taskfile.yaml

tasks:
  default: task -l
```

#### 4.2. Prune Flux Taskfile
Reduce `Flux/Taskfile.yaml` to essentials:
*   `verify`: `flux reconcile kustomization cluster ...`
*   `sync`: `flux reconcile source git flux-system ...`

## 6. Execution Order (Risk-Based Roadmap)

1.  **Week 1: Housekeeping (Low Risk)**
    *   Clean Taskfiles.
    *   Migrate `HelmRepository` → `OCIRepository` (Low risk).
2.  **Week 2: Flux Structure & Components (Medium Risk)**
    *   Implement Global Patches in `flux/apps.yaml`.
    *   Refactor `Templates` → `Components`.
    *   Simplify individual `ks.yaml` files.
3.  **Week 3: Network on Edge (High Risk)**
    *   Install Gateway API CRDs on `edge` cluster.
    *   Configure Traefik for Gateway API.
    *   Migrate one app to `HTTPRoute` on `edge`.
4.  **Week 4: Monitoring (Medium Risk)**
    *   Deploy Gatus Sidecar.
    *   Verify auto-discovery works with the migrated app.
