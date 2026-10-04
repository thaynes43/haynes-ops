# PiKVM

See `./pikvm.yaml` for example config - I am manually keeping this in sync with what is on the device.

## Editing Config

### Apply `pikvm.yaml` in one paste

`pikvm.yaml` on `main` is the source of truth. On the PiKVM web terminal run `su -`
(enter the root password), then paste this single line. It backs up the current
override, downloads the repo copy to a temp file (a failed download changes nothing),
validates it with `kvmd -m`, restarts kvmd, and on any failure restores the old file
and restarts kvmd again. It prints `APPLIED` or `ROLLED_BACK`:

```sh
rw; cp /etc/kvmd/override.yaml /etc/kvmd/override.yaml.bak && curl -fsSL https://raw.githubusercontent.com/thaynes43/haynes-ops/main/pikvm.yaml -o /tmp/pikvm.yaml && cp /tmp/pikvm.yaml /etc/kvmd/override.yaml && { kvmd -m >/dev/null && systemctl restart kvmd && echo APPLIED || { cp /etc/kvmd/override.yaml.bak /etc/kvmd/override.yaml; systemctl restart kvmd; echo ROLLED_BACK; }; }; ro
```

TESmart mapping: kvmd `serverN` / pin `N` is switch input PC`N+1` (server0 = PC1 ...
server15 = PC16, the switch is 16-port). Edit `pikvm.yaml` in git, never on the device.

### Manual edit

To edit config by hand in the pikvm terminal:

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
