# Namespace Alerts

I keep deleting stuff like this from `rook-ceph`'s namespace:

```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/notification.toolkit.fluxcd.io/provider_v1beta3.json
apiVersion: notification.toolkit.fluxcd.io/v1beta3
kind: Provider
metadata:
  name: alert-manager
  namespace: rook-ceph
spec:
  type: alertmanager
  address: http://alertmanager-operated.observability.svc.cluster.local:9093/api/v2/alerts/
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/notification.toolkit.fluxcd.io/alert_v1beta3.json
apiVersion: notification.toolkit.fluxcd.io/v1beta3
kind: Alert
metadata:
  name: alert-manager
  namespace: rook-ceph
spec:
  providerRef:
    name: alert-manager
  eventSeverity: error
  eventSources:
    - kind: HelmRelease
      name: "*"
  exclusionList:
    - "error.*lookup github\\.com"
    - "error.*lookup raw\\.githubusercontent\\.com"
    - "dial.*tcp.*timeout"
    - "waiting.*socket"
  suspend: false
```

Need to get alerts going right after barebones smart home. 