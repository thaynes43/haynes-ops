#!/usr/bin/env python3
"""Compare disposable UserInfo canary results from the base and patched images.

Reads JSON the canary already redacted. Prints booleans and counts only.
A patched-only run cannot pass: baseline visibility has to be true.
"""
import argparse
import json
from pathlib import Path

SOURCE_SHA = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
PATCHED_SHA = "ae1494c6c145d094fd1ad473517f4c0e5fbb8fdfc7b145c651235f29f1871b97"
SHAPES = ("opaque", "jwt-shaped")
BRANCHES = ("missing", "expired", "revoked", "success")
# Synthetic noncredentials. Presence in a result file fails the comparison.
# Do not print these values or the result body that contained them.
MARKERS = (
    "PORTAL178_SYNTHETIC_OPAQUE_NONCREDENTIAL",
    "eyJhbGciOiJub25lIn0.eyJzdWIiOiJTWU5USEVUSUMifQ.SYNTHETIC",
)


def load_result(path):
    raw = Path(path).read_text()
    if any(marker in raw for marker in MARKERS):
        raise SystemExit("canary result contains a synthetic marker")
    data = json.loads(raw)
    if not data.get("proof"):
        raise SystemExit("canary result is not a proof")
    return data


def case_map(result):
    found = {}
    for case in result["cases"]:
        found[(case["shape"], case["branch"])] = case
    if set(found) != {(shape, branch) for shape in SHAPES for branch in BRANCHES}:
        raise SystemExit("canary cases are not the expected matrix")
    return found


def compare(results):
    """results maps (artifact, level) to a canary JSON object."""
    expected = {
        ("base", "DEBUG"): SOURCE_SHA,
        ("patched", "DEBUG"): PATCHED_SHA,
        ("base", "INFO"): SOURCE_SHA,
        ("patched", "INFO"): PATCHED_SHA,
    }
    if set(results) != set(expected):
        raise SystemExit("expected base and patched at DEBUG and INFO")
    summary = []
    for key, result in results.items():
        artifact, level = key
        if result.get("artifact") != artifact or result.get("logLevel") != level:
            raise SystemExit("canary artifact or level does not match its file")
        if result.get("utilsSha256") != expected[key]:
            raise SystemExit("utils hash does not match the artifact")
        if result.get("realTokens") is not False or result.get("databaseWrites") is not False:
            raise SystemExit("canary claimed a real token or a database write")
        if result.get("productionTokenLeakProven") is not False:
            raise SystemExit("synthetic canary cannot prove a production token leak")
        if bool(result.get("debugEnabled")) is not (level == "DEBUG"):
            raise SystemExit("observed log level does not match the run")
        summary.append(artifact + ":" + level)
    base_debug = case_map(results[("base", "DEBUG")])
    patched_debug = case_map(results[("patched", "DEBUG")])
    base_info = case_map(results[("base", "INFO")])
    patched_info = case_map(results[("patched", "INFO")])
    for shape in SHAPES:
        for branch in BRANCHES:
            runs = {
                "baseDebug": base_debug[(shape, branch)],
                "patchedDebug": patched_debug[(shape, branch)],
                "baseInfo": base_info[(shape, branch)],
                "patchedInfo": patched_info[(shape, branch)],
            }
            for item in runs.values():
                if item["eventCanaryVisible"] or item["responseCanaryVisible"]:
                    raise SystemExit("canary reached an event or response")
                if branch != "success" and not item["denialUnchanged"]:
                    raise SystemExit("denial response changed")
                if branch == "success" and not item["successSubject"]:
                    raise SystemExit("successful userinfo subject was not observed")
                if item["eventPathExercised"] is not (branch == "revoked"):
                    raise SystemExit("suspicious event path did not follow revocation")
            denial = branch != "success"
            if denial and not runs["baseDebug"]["logMessageObserved"]:
                raise SystemExit("baseline debug did not observe the branch")
            if denial and not runs["baseDebug"]["logCanaryVisible"]:
                raise SystemExit("baseline debug did not show the marker; test is insensitive")
            if runs["patchedDebug"]["logCanaryVisible"] or runs["patchedInfo"]["logCanaryVisible"]:
                raise SystemExit("patched formatted log still shows the marker")
            if branch == "success":
                for item in runs.values():
                    if item["status"] != 200 or item["logCanaryVisible"] or item["logMessageObserved"]:
                        raise SystemExit("successful response changed")
                continue
            for item in (runs["baseDebug"], runs["patchedDebug"]):
                if item["status"] != 401 or not item["logMessageObserved"] or not item["formattedJson"]:
                    raise SystemExit("debug denial was not formatted")
                if item["severityObserved"] is not True:
                    raise SystemExit("debug severity changed")
            if branch == "revoked":
                for item in (runs["baseInfo"], runs["patchedInfo"]):
                    if item["status"] != 401 or not item["logMessageObserved"] or not item["formattedJson"]:
                        raise SystemExit("info did not keep the revoked warning")
                    if item["severityObserved"] is not True:
                        raise SystemExit("warning severity changed")
                if not runs["baseInfo"]["logCanaryVisible"]:
                    raise SystemExit("baseline info warning did not show the marker")
            else:
                for item in (runs["baseInfo"], runs["patchedInfo"]):
                    if item["status"] != 401 or item["logMessageObserved"] or item["logCanaryVisible"]:
                        raise SystemExit("info did not hide the debug denial")
            if runs["patchedDebug"]["logCanaryVisible"] is not False:
                raise SystemExit("patched debug still shows the marker")
    return {"compared": True, "runs": sorted(summary), "cases": len(SHAPES) * len(BRANCHES) * 4}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-debug", required=True)
    parser.add_argument("--base-info", required=True)
    parser.add_argument("--patched-debug", required=True)
    parser.add_argument("--patched-info", required=True)
    args = parser.parse_args()
    report = compare({
        ("base", "DEBUG"): load_result(args.base_debug),
        ("base", "INFO"): load_result(args.base_info),
        ("patched", "DEBUG"): load_result(args.patched_debug),
        ("patched", "INFO"): load_result(args.patched_info),
    })
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
