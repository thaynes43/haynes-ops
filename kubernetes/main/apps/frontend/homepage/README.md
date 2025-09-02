Homepage is a highly configurable service so I am creating some docs to remember what to do.

## Config 

- [This example](https://github.com/rafaribe/home-ops/blob/main/kubernetes/main/apps/services/homepage/app/configuration.yaml) is pretty comprehensive.

## Secrets
See [this](# https://github.com/rafaribe/home-ops/blob/main/kubernetes/main/apps/services/homepage/app/externalsecret.yaml) repo for some great examples of what types of secrets are needed.

First pull the API key or usr/pw you need into the external secret env for homepage:

```yaml
      data:
        # Immich
        HOMEPAGE_VAR_IMMICH_API_KEY: "{{ .IMMICH_API_KEY }}"
```

Then reference this in your `IngressRoute` / `Route` / `Ingress`

```yaml
    # Homepage
    gethomepage.dev/href: "https://immich.haynesnetwork.com"
    gethomepage.dev/enabled: "true"
    gethomepage.dev/description: Photo management platform
    gethomepage.dev/group: Media
    gethomepage.dev/name: Immich
    gethomepage.dev/app: immich-server
    gethomepage.dev/icon: sh-immich.svg
    gethomepage.dev/widget.type: "immich"
    gethomepage.dev/widget.url: "https://immich.haynesnetwork.com"
    gethomepage.dev/widget.key: "{{ `{{HOMEPAGE_VAR_IMMICH_API_KEY}}` }}"
    gethomepage.dev/widget.version: "2"
```