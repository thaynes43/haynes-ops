# PiKVM

See `./pikvm.yaml` for example config - I am manually keeping this in sync with what is on the device.

## Editing Config

To edit config in pikvm terminal:

```yaml
su -
rw
nano /etc/kvmd/override.yaml
ro
exit
```

### Configure TeSMART

TODO this was tricky document before moving

### Configure WOL

## Monitoring

See notes [here](https://onedr0p.github.io/home-ops/) which I have copied below.

## Monitoring

This is done ON the KVM! 

### Install node-exporter

```sh
pacman -S prometheus-node-exporter
systemctl enable --now prometheus-node-exporter
```

### Install promtail

1. Install promtail

    ```sh
    pacman -S promtail
    systemctl enable promtail
    ```

2. Override the promtail systemd service

    ```sh
    mkdir -p /etc/systemd/system/promtail.service.d/
    cat >/etc/systemd/system/promtail.service.d/override.conf <<EOL
    [Service]
    Type=simple
    ExecStart=
    ExecStart=/usr/bin/promtail -config.file /etc/loki/promtail.yaml
    EOL
    ```

3. Add or replace the file `/etc/loki/promtail.yaml`

    ```yaml
    server:
      log_level: info
      disable: true

    client:
      url: "https://loki.devbu.io/loki/api/v1/push"

    positions:
      filename: /tmp/positions.yaml

    scrape_configs:
      - job_name: journal
        journal:
          path: /run/log/journal
          max_age: 12h
          labels:
            job: systemd-journal
        relabel_configs:
          - source_labels: ["__journal__systemd_unit"]
            target_label: unit
          - source_labels: ["__journal__hostname"]
            target_label: hostname
    ```

4. Start promtail

    ```sh
    systemctl daemon-reload
    systemctl enable --now promtail.service
    ```
