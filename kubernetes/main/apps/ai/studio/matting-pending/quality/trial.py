"""One offline request to the installed matting profile. Emit numeric evidence only."""

import http.client
import os
import re
import resource
import sys
from contextlib import ExitStack
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PIL import Image

from haynes_studio.matting.backend import DIRECTORY, MAX_PNG_BYTES, Engine, _read_file, envelope, verify_result
from haynes_studio.matting.pixels import MAX_SOURCE_BYTES, decode_source
from haynes_studio.matting.profile import MEMORY_LIMIT_BYTES, PROFILE_ID, PROVIDER, TIMEOUT, MatteError, Profile
from haynes_studio.matting.service import STATUS, listening, local_call
from haynes_studio.store import canonical_json

SCRATCH = Path("/matting")
WEIGHTS = Path("/weights")
OUTPUT = Path("/output")
INPUT = Path("/input")


def check(condition, code):
    if not condition:
        raise MatteError(code)


def read_at(path, name, limit, code):
    fd = os.open(path, DIRECTORY)
    try:
        return _read_file(fd, name, limit, code)
    finally:
        os.close(fd)


def create_at(directory, name, payload):
    flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL
    try:
        with os.fdopen(os.open(name, flags, 0o640, dir_fd=directory), "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise MatteError("trial.output_exists") from None
    except OSError:
        raise MatteError("trial.output_unavailable") from None


def request_for(data, source_id, production_id, job_id, digest, height):
    with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
        source = {"asset_version_id": source_id, "production_id": production_id,
                  "sha256": sha256(data).hexdigest(), "bytes": len(data),
                  "media_type": Image.MIME[image.format], "width": image.width, "height": image.height}
    request = {
        "schema": "studio.matte-request/v1", "job_id": job_id, "source": source,
        "profile_id": PROFILE_ID, "profile_sha256": digest,
        # The imported still's backing colour space is unknown.
        "parameters": {"chroma_rgb": None, "target_height_px": height},
    }
    payload = {"attempt_id": uuid4().hex, "request": request}
    envelope(payload)
    decode_source(data, source).close()
    return payload


def quality_trial(arguments, report):
    check(len(arguments) == 7, "trial.arguments_invalid")
    name, source_hash, source_id, production_id, job_id, height, pin = arguments
    for digest in (source_hash, pin):
        check(re.fullmatch(r"[0-9a-f]{64}", digest), "trial.arguments_invalid")
    check(name in (source_hash + ".png", source_hash + ".jpg")
          and re.fullmatch(r"[1-9][0-9]{0,3}", height) and int(height) <= 2048, "trial.arguments_invalid")
    report["source_sha256"] = source_hash
    try:
        data = read_at(INPUT, name, MAX_SOURCE_BYTES, "trial.input_invalid")
    except OSError:
        raise MatteError("trial.input_invalid") from None
    check(sha256(data).hexdigest() == source_hash, "trial.input_hash_mismatch")
    try:
        request = request_for(data, source_id, None if production_id == "-" else production_id,
                              job_id, pin, int(height))
    except (OSError, ValueError, MatteError, SyntaxError, Image.DecompressionBombError, Warning):
        raise MatteError("trial.input_invalid") from None
    report["source_bytes"] = len(data)
    try:
        destination = os.open(OUTPUT, DIRECTORY)
    except OSError:
        raise MatteError("trial.output_unavailable") from None
    try:
        check(not os.listdir(destination), "trial.output_exists")
        run_request(request, data, destination, report)
    finally:
        os.close(destination)


def run_request(payload, data, destination, report):
    try:
        profile = Profile.installed()
    except (KeyError, OSError, ValueError, MatteError):
        raise MatteError("trial.service_not_ready") from None
    digest = sha256(profile.manifest()).hexdigest()
    request = payload["request"]
    check(digest == request["profile_sha256"], "trial.service_not_ready")
    report.update(profile_sha256=digest, image_digest=profile.image_digest, provider=PROVIDER,
                  cpu_arena=profile.session.cpu_arena)
    attempt = SCRATCH / payload["attempt_id"]
    attempt.mkdir(mode=0o700)
    with (attempt / "input").open("xb") as source:
        source.write(data)
    memory = report["worker_memory"]
    started = monotonic()
    with ExitStack() as running:
        socket = SCRATCH / "matte.sock"
        try:
            engine = running.enter_context(Engine(SCRATCH, WEIGHTS, profile, memory=memory))
            running.enter_context(listening(socket, engine))
        except MatteError as error:
            raise MatteError("trial.timeout" if error.code == TIMEOUT else "trial.service_not_ready") from None
        except OSError:
            raise MatteError("trial.service_not_ready") from None
        report["startup_seconds"] = round(monotonic() - started, 3)
        try:
            status, readiness = local_call(socket, "GET", "/v1/ready")
        except TimeoutError:
            raise MatteError("trial.timeout") from None
        except (OSError, http.client.HTTPException, ValueError, MatteError):
            raise MatteError("trial.service_not_ready") from None
        check(status == 200 and readiness == {"profile_id": PROFILE_ID, "profile_sha256": digest,
                                              "provider": PROVIDER}, "trial.service_not_ready")
        before_request = monotonic()
        try:
            status, receipt = local_call(socket, "POST", "/v1/matte", payload)
        except TimeoutError:
            raise MatteError("trial.timeout") from None
        except (OSError, http.client.HTTPException, ValueError, MatteError):
            raise MatteError("trial.verification_failed") from None
        report["request_seconds"] = round(monotonic() - before_request, 3)
        if status != 200:
            code = receipt.get("code") if isinstance(receipt, dict) else None
            check(isinstance(code, str) and STATUS.get(code) == status, "trial.verification_failed")
            report["service_code"] = code
            raise MatteError("trial.timeout" if code == TIMEOUT else "trial.service_refused")

    samples = [memory.get("session_creation"), *(memory.get("requests") or [])]
    check(len(samples) == 2 and all(samples), "trial.verification_failed")
    report["peak_rss_upper_bound_bytes"] = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        + max(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
              *(sample["peak_rss_bytes"] for sample in samples))
    )
    report["memory_limit_bytes"] = MEMORY_LIMIT_BYTES
    check(report["peak_rss_upper_bound_bytes"] <= MEMORY_LIMIT_BYTES, "trial.verification_failed")

    try:
        artifacts = {kind: read_at(attempt, kind + ".png", MAX_PNG_BYTES, "trial.verification_failed")
                     for kind in ("cutout", "mask")}
        verify_result(request, receipt, artifacts, profile)
    except (OSError, MatteError):
        raise MatteError("trial.verification_failed") from None
    report.update(input_signature=receipt["input_signature"], shape=receipt["shape"],
                  artifacts=receipt["artifacts"])
    # Keep this policy in step with Studio's studio.cast-matte-size/v1. The
    # installed image provides the receipt and pixel verifier.
    shape = receipt["shape"]
    left, top, right, bottom = shape["bbox_xyxy"]
    width, height = right - left, bottom - top
    source = request["source"]
    check(0 < left < right < source["width"] and 0 < top < bottom < source["height"]
          and height >= 64 and request["parameters"]["target_height_px"] <= 4 * height
          and height <= 4 * width and width <= 2 * height
          and 4 * shape["foreground_area_px"] >= width * height, "trial.verification_failed")
    for kind, data in artifacts.items():
        create_at(destination, kind + ".png", data)
    # The report is the completion marker and is created last. Keep the exact
    # receipt in private storage, while the log carries only numeric evidence.
    create_at(destination, "report.json", canonical_json({**report, "passed": True, "receipt": receipt}))
    report["passed"] = True


def main():
    report = {"passed": False, "worker_memory": {}}
    try:
        quality_trial(sys.argv[1:], report)
    except MatteError as error:
        report.update(passed=False, code=error.code)
    except Exception:
        # Raw errors can include the private input name.
        report.update(passed=False, code="trial.command_failed")
    print(canonical_json(report).decode("utf-8"), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
