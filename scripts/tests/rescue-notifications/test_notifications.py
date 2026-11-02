# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Offline source fixtures; no cluster, provider, credentials or notifications."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
APPS = ROOT / "kubernetes/main/apps"
RESPONDER = APPS / "upgrade-agent/alert-responder/app"
HELPER = RESPONDER / "resources/rescue-incident.sh"
SCRIPT = RESPONDER / "resources/respond.sh"


def alert():
    return {
        "fingerprint": "stable-fingerprint", "startsAt": "2026-10-29T08:00:00Z",
        "labels": {
            "alertname": "DevEnvRescueFailed", "severity": "critical",
            "session": "fixture-session", "session_uid": "11111111-1111-1111-1111-111111111111",
            "session_generation": "3", "reason": "BootstrapInterrupted", "risk_key": "a" * 64,
            "owner_uid": "", "pending_delete": "true",
        },
        "annotations": {"description": "Git preservation is not proved; transcript status unknown"},
    }


def helper(function, value, *args):
    return subprocess.run(
        ["bash", "-c", 'source "$1"; fn="$2"; shift 2; "$fn" "$@"', "fixture", str(HELPER), function, json.dumps(value), *map(str, args)],
        capture_output=True, text=True, timeout=5,
    )


def eligible(values, handled="", unreadable=False):
    # Execute the actual production eligibility loop with deterministic state and
    # cooldown fixtures. Nothing above/below the loop (network/model) is run.
    loop = SCRIPT.read_text().split("elig='[]'", 1)[1].split("# Work units:", 1)[0]
    prelude = '''source "$1"
log() { :; }
state_has() { [ "$UNREADABLE" = 1 ] || [ "$1" = "$HANDLED" ]; }
cooldown_active() { return 0; }
NOW=1793620800
MAX_AGE_HOURS=24
candidates="$2"; count="$(jq length <<< "$candidates")"
HANDLED="$3"; UNREADABLE="$4"
elig='[]'
'''
    result = subprocess.run(
        ["bash", "-c", prelude + loop + '\nprintf "%s" "$elig"', "fixture", str(HELPER), json.dumps(values), handled, "1" if unreadable else "0"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class IncidentTests(unittest.TestCase):
    def test_retry_text_and_attempt_do_not_mint_incident(self):
        original = alert()
        retry = copy.deepcopy(original)
        retry["annotations"] = {"description": "retry at a different time", "latestAttempt": "2026-11-02T12:00:00Z"}
        a = helper("incident_claim_key", original).stdout
        self.assertEqual(a, helper("incident_claim_key", retry).stdout)
        self.assertEqual(eligible([retry], a.strip()), [])

    def test_material_risk_changes_bypass_old_claim_and_global_cooldown(self):
        original = alert()
        key = helper("incident_claim_key", original).stdout.strip()
        for field, value in {
            "reason": "FetchFailed", "risk_key": "b" * 64, "session_generation": "4",
            "session_uid": "44444444-4444-4444-4444-444444444444", "pending_delete": "false",
        }.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed["labels"][field] = value
                self.assertNotEqual(helper("incident_claim_key", changed).stdout.strip(), key)
                self.assertEqual(len(eligible([changed], key)), 1)

    def test_owned_incident_dedupes_but_lost_or_invalid_owner_is_eligible(self):
        value = alert()
        value["labels"]["owner_uid"] = "22222222-2222-2222-2222-222222222222"
        self.assertEqual(helper("rescue_incident_owned", value).returncode, 0)
        self.assertEqual(eligible([value]), [])
        for field, replacement in {"owner_uid": "", "risk_key": "unclassified", "reason": "FetchFailed"}.items():
            changed = copy.deepcopy(value)
            changed["labels"][field] = replacement
            self.assertNotEqual(helper("rescue_incident_owned", changed).returncode, 0)
            self.assertEqual(len(eligible([changed])), 1)

    def test_clear_then_refire_is_distinct_and_spend_state_stays_fail_closed(self):
        value = alert()
        key = helper("incident_claim_key", value).stdout.strip()
        value["startsAt"] = "2026-11-02T12:00:00Z"
        self.assertEqual(len(eligible([value], key)), 1)
        self.assertEqual(eligible([value], unreadable=True), [])

    def test_generic_and_legacy_alert_policy_is_unchanged(self):
        value = alert()
        value["labels"] = {"alertname": "OtherCritical", "severity": "critical"}
        self.assertNotEqual(helper("rescue_identity_qualified", value).returncode, 0)
        self.assertEqual(eligible([value]), [])  # Old generic age policy.
        value["startsAt"] = "2026-11-02T12:00:00Z"
        self.assertEqual(eligible([value]), [])  # Existing generic cooldown.
        value["labels"]["alertname"] = "DevEnvRescueFailed"
        value["labels"]["owner_uid"] = "22222222-2222-2222-2222-222222222222"
        self.assertNotEqual(helper("rescue_incident_owned", value).returncode, 0)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        outer = yaml.safe_load((APPS / "observability/kube-prometheus-stack/app/externalsecret.yaml").read_text())
        self.config = yaml.safe_load(outer["spec"]["target"]["template"]["data"]["alertmanager.yaml"])

    def receiver(self, labels):
        for route in self.config["route"]["routes"]:
            matches = True
            for matcher in route.get("matchers", []):
                key, operator, value = re.fullmatch(r'([a-z_]+)\s*(=~|=)\s*"(.*)"', matcher).groups()
                actual = labels.get(key, "")
                matches &= bool(re.fullmatch(value, actual)) if operator == "=~" else actual == value
            if matches:
                return route
        return self.config["route"]

    def test_unowned_new_risk_and_unrelated_critical_keep_urgent_receiver(self):
        value = alert()["labels"]
        self.assertEqual(self.receiver(value)["receiver"], "pushover")
        value["owner_uid"] = "22222222-2222-2222-2222-222222222222"
        route = self.receiver(value)
        self.assertEqual(route["receiver"], "pushover-rescue-owned")
        self.assertEqual(route["repeat_interval"], "168h")
        receiver = next(r for r in self.config["receivers"] if r["name"] == route["receiver"])
        self.assertEqual(receiver["pushover_configs"][0]["priority"], "0")
        self.assertTrue(receiver["pushover_configs"][0]["send_resolved"])
        for field, replacement in {"owner_uid": "", "risk_key": "unclassified", "reason": "FetchFailed", "session_uid": ""}.items():
            changed = {**value, field: replacement}
            self.assertEqual(self.receiver(changed)["receiver"], "pushover")
        self.assertEqual(self.receiver({"alertname": "OtherCritical", "severity": "critical"})["receiver"], "pushover")

    def test_rule_preserves_reason_identity_and_has_old_operator_fallback(self):
        rulefile = yaml.safe_load((APPS / "dev-env-system/operator/app/prometheusrule.yaml").read_text())
        rule = rulefile["spec"]["groups"][0]["rules"][0]
        expression = rule["expr"]
        self.assertIn("session_uid, session_generation, reason, risk_key, owner_uid, pending_delete", expression)
        self.assertIn("unless on (session) dev_env_session_rescue_notification", expression)
        self.assertEqual(rule["labels"]["severity"], "critical")
        self.assertEqual(rule["for"], "0m")
        self.assertNotIn("keep_firing_for", rule)
        self.assertNotIn("latest_attempt", expression)
        self.assertIn("transcript failure", rule["annotations"]["description"])
        self.assertNotIn("human can exec", rule["annotations"]["description"])


@unittest.skipUnless(os.environ.get("PROMTOOL") or shutil.which("promtool"), "set PROMTOOL for actual rule-timing evaluation")
class PrometheusTimingTests(unittest.TestCase):
    def test_risk_churn_and_owner_loss_never_restart_or_retain_alert(self):
        source = yaml.safe_load((APPS / "dev-env-system/operator/app/prometheusrule.yaml").read_text())
        rule = source["spec"]["groups"][0]["rules"][0]
        variants = []
        first = alert()["labels"]
        first["owner_uid"] = "22222222-2222-2222-2222-222222222222"
        variants.append(first)
        variants.append({**first, "owner_uid": ""})
        variants.append({**variants[-1], "reason": "FetchFailed", "risk_key": "unclassified"})
        variants.append({**variants[-1], "session_uid": "44444444-4444-4444-4444-444444444444"})

        def selector(name, labels):
            return name + "{" + ",".join(f'{k}={json.dumps(v)}' for k, v in sorted(labels.items()) if v != "") + "}"

        inputs, evaluations = [], []
        for i, labels in enumerate(variants):
            labels = {k: v for k, v in labels.items() if k not in ("alertname", "severity")}
            start = i * 6
            samples = ["_"] * start + ["1"] * 6 + ["stale"] + ["_"] * (24 - start - 6)
            inputs.append({"series": selector("dev_env_session_rescue_notification", labels), "values": " ".join(samples)})
            for minute in (start, start + 5):
                current = {**labels, "alertname": "DevEnvRescueFailed", "alertstate": "firing", "severity": "critical"}
                evaluations.append({"expr": 'ALERTS{alertname="DevEnvRescueFailed",alertstate="firing"}', "eval_time": f"{minute}m", "exp_samples": [{"labels": selector("ALERTS", current), "value": 1}]})
        # The old continuous metric remains available throughout. It must not
        # duplicate new identity, and must notify immediately when no new metric
        # exists (old operator compatibility at minute24).
        old = {"session": first["session"], "reason": "NotProven"}
        inputs.append({"series": selector("dev_env_session_rescue_failed", old), "values": "1x24"})
        evaluations.append({"expr": 'ALERTS{alertname="DevEnvRescueFailed",alertstate="firing"}', "eval_time": "24m", "exp_samples": [{"labels": selector("ALERTS", {**old, "alertname": "DevEnvRescueFailed", "alertstate": "firing", "severity": "critical"}), "value": 1}]})
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            rules = directory / "rules.yml"
            rules.write_text(yaml.safe_dump({"groups": [{"name": "fixture", "interval": "1m", "rules": [rule]}]}))
            fixture = directory / "test.yml"
            fixture.write_text(yaml.safe_dump({"rule_files": [str(rules)], "evaluation_interval": "1m", "tests": [{"interval": "1m", "input_series": inputs, "promql_expr_test": evaluations}]}))
            result = subprocess.run([os.environ.get("PROMTOOL") or shutil.which("promtool"), "test", "rules", str(fixture)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
