"""Offline promtool scenarios against the actual GitOps rules, no hardware writes."""
import json

KEY = dict(job="smartctl-exporter-haynestower", instance="haynestower", device="nvme0", role="nas")
CRITICAL = "SmartDeviceCriticalWarning"
SMART = "SmartDeviceTestFailed"


def scenario(name, *, serial="2331E865B74C", device="nvme0", warning=4,
             smart=0, exit_status=8, media=0, errors=183, temperature=35,
             spare=100, identity=True, omit=None, legacy=None,
             expected=(), instance="haynestower", job="smartctl-exporter-haynestower",
             duplicate=False, info_device=None, info_job=None, info_instance=None):
    labels = dict(KEY, device=device, instance=instance, job=job)
    series = []

    def add(metric, value, **extra):
        if metric == omit:
            return
        ls = dict(labels, **extra)
        sel = ",".join(f'{k}="{v}"' for k, v in ls.items())
        series.append(dict(series=f"{metric}{{{sel}}}", values=f"{value}+0x21"))

    for metric, value in (
        ("smartctl_device_critical_warning", warning),
        ("smartctl_device_smart_status", smart),
        ("smartctl_device_smartctl_exit_status", exit_status),
        ("smartctl_device_media_errors", media),
        ("smartctl_device_num_err_log_entries", errors),
        ("smartctl_device_available_spare", spare),
        ("smartctl_device_available_spare_threshold", 5),
    ):
        add(metric, value)
    add("smartctl_device_temperature", temperature, temperature_type="current")
    # Rated limits are not live temperatures.
    add("smartctl_device_temperature", 95, temperature_type="critical_limit_max")
    if identity:
        extra = dict(serial_number=serial, model_name="CT2000P3PSSD8",
                     device=info_device or device, job=info_job or job,
                     instance=info_instance or instance)
        add("smartctl_device", 1, **extra)
        if duplicate:
            add("smartctl_device", 1, **dict(extra, serial_number="replacement"))
    if legacy is not None:
        add("smartctl_device_status", legacy)
    alerts = []
    for alert in (CRITICAL, SMART, "SmartDeviceHighTemperature",
                  "SmartDeviceMediaErrors", "SmartDeviceAvailableSpareUnderThreadhold"):
        found = []
        if alert in expected:
            exp_labels = dict(labels, scope="host", severity="critical")
            if alert == "SmartDeviceHighTemperature":
                exp_labels["temperature_type"] = "current"
            found.append(dict(exp_labels=exp_labels, exp_annotations={}))
        alerts.append(dict(eval_time="20m", alertname=alert, exp_alerts=found))
    return dict(name=name, interval="1m", input_series=series, alert_rule_test=alerts)


BOTH = (CRITICAL, SMART)
scenarios = [
    scenario("B74C current nvme0 acknowledged"),
    scenario("B76D current nvme1 acknowledged", serial="2331E865B76D", device="nvme1"),
    scenario("B74C old nvme2 acknowledged", device="nvme2"),
    scenario("B76D renumbered nvme9 acknowledged", serial="2331E865B76D", device="nvme9"),
    scenario("unrelated serial at old excluded nvme2", device="nvme2", serial="2331E865B742", expected=BOTH),
    scenario("unrelated host", instance="other-nas", expected=BOTH),
    scenario("unrelated scrape job", job="other-exporter", expected=BOTH),
    scenario("missing info", identity=False, expected=BOTH),
    scenario("missing serial", serial="", expected=BOTH),
    scenario("ambiguous device identity", duplicate=True, expected=BOTH),
    scenario("identity belongs to other device", info_device="nvme8", expected=BOTH),
    scenario("identity belongs to other job", info_job="other-exporter", expected=BOTH),
    scenario("identity belongs to other instance", info_instance="other-nas", expected=BOTH),
    scenario("warning cleared but independent SMART failure", warning=0, expected=(SMART,)),
    scenario("missing warning but independent SMART failure", omit="smartctl_device_critical_warning", expected=(SMART,)),
    scenario("new smartctl exit bit", exit_status=24, expected=BOTH),
    scenario("unexpected overall status", smart=2, expected=BOTH),
    scenario("independent legacy failure", legacy=0, expected=(SMART,)),
    scenario("new error log entry", errors=184, expected=BOTH),
    scenario("error log count reset", errors=0, expected=BOTH),
    scenario("media error", media=1, expected=(*BOTH, "SmartDeviceMediaErrors")),
    scenario("real overtemperature", temperature=66, expected=("SmartDeviceHighTemperature",)),
    scenario("spare below threshold", spare=4, expected=("SmartDeviceAvailableSpareUnderThreadhold",)),
    scenario("healthy unrelated device", serial="2331E865B710", warning=0, smart=1, exit_status=0),
]
for mask in (1, 2, 6, 8, 12, 16, 32, 36):
    scenarios.append(scenario(f"new or combined warning mask {mask}", warning=mask, expected=BOTH))
for metric in ("smartctl_device_smart_status", "smartctl_device_smartctl_exit_status",
               "smartctl_device_media_errors", "smartctl_device_num_err_log_entries"):
    expected = (CRITICAL,) if metric == "smartctl_device_smart_status" else BOTH
    scenarios.append(scenario(f"missing prerequisite {metric}", omit=metric, expected=expected))
# Exercise both identities together, then transitions after an acknowledged period.
pair = scenario("both physical cache drives together")
pair["input_series"] += scenario("second", serial="2331E865B76D", device="nvme1")["input_series"]
scenarios.append(pair)
for name, metric, after in (
    ("identity disappears after exemption", "smartctl_device{", "stale _x30"),
    ("extra bit arrives after exemption", "smartctl_device_critical_warning{", "12+0x30"),
    ("error log grows after exemption", "smartctl_device_num_err_log_entries{", "184+0x30"),
):
    case = scenario(name, expected=BOTH)
    for series in case["input_series"]:
        if series["series"].startswith(metric):
            initial = series["values"].split("+")[0]
            series["values"] = f"{initial}+0x9 {after}"
        else:
            series["values"] = series["values"].replace("x21", "x40")
    for check in case["alert_rule_test"]:
        check["eval_time"] = "30m"
    # Pin suppression before the transition and the full 15m hold after it.
    for when in ("5m", "24m"):
        case["alert_rule_test"].extend(
            dict(eval_time=when, alertname=alert, exp_alerts=[]) for alert in BOTH
        )
    scenarios.append(case)
print(json.dumps(dict(rule_files=["rules-noann.yaml"], evaluation_interval="1m", tests=scenarios), indent=2))
