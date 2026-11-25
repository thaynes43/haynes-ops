"""Fill the staged comparison Job with an approved locator and fresh run suffix."""

import argparse
from copy import deepcopy
import json
import re
import sys

LOCATOR = re.compile(
    r"(?:library|productions/prd_[0-7][0-9A-HJKMNP-TV-Z]{25})"
    r"/sha256/(?P<shard>[a-f0-9]{2})/(?P<digest>(?P=shard)[a-f0-9]{62})\.(?P<ext>jpg|png|mp4)"
)
DELIVERY_ID = re.compile(r"dlv_[0-7][0-9A-HJKMNP-TV-Z]{25}")


def comparison_job(template, *, source, run, index=None, timestamp=None, master=None,
                   delivery_id=None, orientation="portrait", plate=False, rois=None, probe=False):
    locator = LOCATOR.fullmatch(source)
    reference = LOCATOR.fullmatch(master) if master is not None else None
    if locator is None or not re.fullmatch(r"[a-f0-9]{12}", run):
        raise ValueError("invalid input locator or run suffix")
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("invalid orientation")
    is_video = locator["ext"] == "mp4"
    if (master is None) != (delivery_id is None) or (
            delivery_id is not None and not DELIVERY_ID.fullmatch(delivery_id)):
        raise ValueError("master requires a verified delivery ID")
    if is_video:
        if plate or (master is not None and (reference is None or reference["ext"] != "mp4")):
            raise ValueError("invalid proxy or master options")
        args = ["--proxy", source]
        if index is not None and timestamp is None and re.fullmatch(r"[0-9]{1,10}", index):
            args += ["--frame-index", index]
        elif index is None and timestamp is not None and re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,9})?", timestamp):
            args += ["--timestamp", timestamp]
        else:
            raise ValueError("choose one valid frame selector")
        if reference is not None:
            args += ["--master", master, "--delivery-id", delivery_id]
    else:
        if probe or any(option is not None for option in (index, timestamp, master)):
            raise ValueError("probe, frame selector and master require an MP4 proxy")
        args = ["--input", source.rsplit("/", 1)[1], "--input-sha256", locator["digest"]]
        if plate:
            args.append("--plate")
    args += ["--orientation", orientation]
    for name, box in (rois or {}).items():
        if name not in {"lettering", "face", "hair"}:
            raise ValueError("invalid ROI name")
        args += [f"--{name}-roi", *map(str, box)]

    job = deepcopy(template)
    role = "probe" if probe else "compare"
    job["metadata"]["name"] = f"studio-upscaling-{role}-{run}"
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["args"] = args
    mounts = {item["name"]: item for item in container["volumeMounts"]}
    shard = source.rsplit("/", 1)[0]
    mounts["source"].update(subPath=f"data/studio/{shard}", mountPath=f"/store/{shard}" if is_video else "/input")
    master_shard = master.rsplit("/", 1)[0] if reference is not None else None
    if master_shard is None or master_shard == shard:
        container["volumeMounts"].remove(mounts["master"])
        pod["volumes"] = [volume for volume in pod["volumes"] if volume["name"] != "master"]
    else:
        mounts["master"].update(subPath=f"data/studio/{master_shard}", mountPath=f"/store/{master_shard}")
    if probe:
        job["spec"]["activeDeadlineSeconds"] = 90
        del pod["initContainers"]
        args.append("--probe")
        container["resources"] = {"requests": {"cpu": "100m", "memory": "128Mi"},
                                  "limits": {"cpu": "1", "memory": "256Mi"}}
        for key, items in (("volumeMounts", container), ("volumes", pod)):
            items[key] = [item for item in items[key] if item["name"] in {"source", "master"}]
    else:
        output = f"upscaling-op461-compare-{run}"
        pod["initContainers"][0]["args"].append(output)
        mounts["jobs"]["subPath"] = f"data/studio/jobs/{output}"
    return job


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "invalid comparison Job arguments\n")


def main():
    parser = Parser(allow_abbrev=False)
    parser.add_argument("--input", dest="source", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--frame-index", dest="index")
    parser.add_argument("--timestamp")
    parser.add_argument("--master")
    parser.add_argument("--delivery-id")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--orientation", default="portrait")
    parser.add_argument("--plate", action="store_true")
    for name in ("lettering", "face", "hair"):
        parser.add_argument(f"--{name}-roi", nargs=4, type=int)
    args = vars(parser.parse_args())
    rois = {name: args.pop(name + "_roi") for name in ("lettering", "face", "hair")}
    try:
        job = comparison_job(json.load(sys.stdin), **args, rois={name: box for name, box in rois.items() if box})
    except (ValueError, KeyError, TypeError, IndexError):
        parser.error(None)
    print(json.dumps(job))


if __name__ == "__main__":
    main()
