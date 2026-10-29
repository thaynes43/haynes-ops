#!/usr/bin/env python3
"""Render notification titles locally; never contacts an Alertmanager or sends pages.

AMTOOL=/path/to/official-amtool python3 scripts/dev-env-recovery-alert-test.py
Uses the same synthetic ExternalSecret unescaping as sigoalumni-alert-tests.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml


def rendered_config(document):
    text = document["spec"]["target"]["template"]["data"]["alertmanager.yaml"]
    for literal in ("{{-", "{{", "}}"):
        text = text.replace('{{ "%s" }}' % literal, literal)
    text = text.replace("{{ .ALERTMANAGER_CLUSTER_MAIN_HEARTBEAT_URL }}", "https://hc.example.invalid/ping")
    return re.sub(r"\{\{ \.([A-Z_]+) \}\}", r"dummy-\1", text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--amtool", default=os.environ.get("AMTOOL", "amtool"))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    rule_path = "kubernetes/main/apps/dev-env-system/operator/app/prometheusrule.yaml"
    config_path = "kubernetes/main/apps/observability/kube-prometheus-stack/app/externalsecret.yaml"

    def old(path):
        return yaml.safe_load(subprocess.check_output(["git", "-C", str(repo), "show", args.base + ":" + path]))

    before_rule = old(rule_path)
    after_rule = yaml.safe_load((repo / rule_path).read_text())
    target = next(rule for group in after_rule["spec"]["groups"] for rule in group["rules"]
                  if rule["alert"] == "DevEnvRescueFailed")
    assert target["expr"] == "max by (session) (dev_env_session_rescue_failed) > 0"
    assert target["for"] == "20m" and target["keep_firing_for"] == "30m"
    assert target["labels"] == {"severity": "critical"}
    assert "recovery failed" in target["annotations"]["summary"]
    assert "BootstrapInterrupted" in target["annotations"]["description"]
    assert "technical condition RescueFailed" in target["annotations"]["description"]
    normalized = copy.deepcopy(after_rule)
    for group in normalized["spec"]["groups"]:
        for rule in group["rules"]:
            if rule["alert"] == "DevEnvRescueFailed":
                original = next(r for g in before_rule["spec"]["groups"] for r in g["rules"]
                                if r["alert"] == rule["alert"])
                rule["annotations"] = original["annotations"]
    assert normalized == before_rule, "rule semantics or another rule changed"

    before_text = rendered_config(old(config_path))
    after_text = rendered_config(yaml.safe_load((repo / config_path).read_text()))
    before = yaml.safe_load(before_text)
    after = yaml.safe_load(after_text)
    normalized = copy.deepcopy(after)
    titles = {}
    for receiver in normalized["receivers"]:
        if receiver["name"] in ("pushover", "pushover-daytime"):
            original = next(r for r in before["receivers"] if r["name"] == receiver["name"])
            for index, config in enumerate(receiver["pushover_configs"]):
                titles[receiver["name"]] = config["title"]
                config["title"] = original["pushover_configs"][index]["title"]
    assert normalized == before, "routing, timing, body, priority or another receiver changed"
    assert titles["pushover"] == titles["pushover-daytime"], "receiver aliases diverged"

    with tempfile.TemporaryDirectory(prefix="recovery-alert-test-") as tmp:
        tmp = Path(tmp)
        config = tmp / "alertmanager.yaml"
        config.write_text(after_text)
        subprocess.run([args.amtool, "check-config", str(config)], check=True, capture_output=True, text=True)
        template = tmp / "title.tmpl"
        template.write_text('{{ define "fixture" }}{{ end }}')
        cases = [("DevEnvRescueFailed", "firing", 1, "[FIRING:1] Recovery failed"),
                 ("DevEnvRescueFailed", "firing", 2, "[FIRING:2] Recovery failed"),
                 ("DevEnvRescueFailed", "resolved", 1, "[RESOLVED] Recovery failed"),
                 ("KubePodCrashLooping", "firing", 1, "[FIRING:1] KubePodCrashLooping"),
                 ("DevEnvRescueFailedOther", "firing", 1, "[FIRING:1] DevEnvRescueFailedOther")]
        for name, status, count, expected in cases:
            data = {"receiver": "pushover", "status": status, "commonLabels": {"alertname": name},
                    "alerts": [{"status": status, "labels": {"alertname": name},
                                "annotations": {"description": "synthetic fixture"}} for _ in range(count)]}
            fixture = tmp / "data.json"
            fixture.write_text(json.dumps(data))
            result = subprocess.run([args.amtool, "--no-version-check", "template", "render",
                                     "--template.glob=" + str(template),
                                     "--template.text=" + titles["pushover"],
                                     "--template.data=" + str(fixture)],
                                    check=True, capture_output=True, text=True).stdout.strip()
            assert result == expected, (name, status, result, expected)
        print("PASS five actual Alertmanager title renderings; firing/resolved/count and unrelated alerts")
    print("PASS rule identifier/expression/timers/severity and all notification routing/body/priority unchanged")


if __name__ == "__main__":
    main()
