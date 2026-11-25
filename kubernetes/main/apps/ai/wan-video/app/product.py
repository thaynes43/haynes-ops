"""Validation, profiles and persistent identity for the Wan product API."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

VERSION = "1.0.0"
MAX_JSON = 65536  # Two 4096-character prompts, including JSON Unicode escapes.
MAX_UPLOAD = 19_999_999
MAX_INPUT_FILES = 4096
MAX_INPUT_BYTES = 4 * 1024**3
MIN_WORKSPACE_FREE = 5 * 1024**3
MAX_SEED = 2**53 - 1
CHUNK = 77
MAX_CHUNKS = 3
STORY = re.compile(r"https://work\.haynesnetwork\.com/work_packages/[1-9][0-9]*")
JOB_ROUTE = re.compile(r"/v1/jobs/(i2v|s2v|foley)/([0-9a-f]{32})(/file)?")
INPUT_ROUTE = re.compile(r"/v1/inputs/([0-9a-f]{64})\.(png|wav)")
SHA256 = re.compile(r"[0-9a-f]{64}")
PNG = b"\x89PNG\r\n\x1a\n"
WAV_RATES = {16000, 22050, 24000, 44100, 48000}
WAV_PCM_SUBFORMAT = bytes.fromhex("01000000 0000 1000 8000 00aa00389b71")
NEGATIVE = (
    "changing doll identity, changing faces, new faces, extra people, extra dolls, human hands, text, "
    "subtitles, watermark, camera shake, fast motion, cuts, scene change, blurry, distorted, melting"
)
PROFILES = {
    "i2v": {"profile": "wan2.2-ti2v-5b", "fps": 24, "steps": 20,
            "max_frames": 121, "timeout_seconds": 1200, "timeout_scope": "render"},
    "s2v": {"profile": "wan2.2-s2v-14b-lightning", "fps": 16, "steps": 4, "cfg": 1,
            "chunk_frames": CHUNK, "max_chunks": MAX_CHUNKS,
            "timeout_seconds": 1200, "timeout_scope": "render"},
    "foley": {"profile": "controlfoley-v1", "sample_rate": 44100, "min_duration": 0.7,
              "max_duration": 8, "timeout_seconds": 600, "timeout_scope": "inference"},
}


class RequestError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status

    def body(self):
        return {"code": self.code, "error": str(self)}


class AdmissionRefusal(RuntimeError):
    def __init__(self, code, error):
        super().__init__(error)
        self.code = code
        self.error = error


def job_error(exc):
    return type(exc).__name__ + ": " + str(exc)[:200]


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def validate(kind, data):
    """Return the canonical typed request used to compare repeated submissions."""
    if not isinstance(data, dict):
        raise RequestError("invalid_request", "Expected an object")
    profile = PROFILES[kind]
    defaults = {"negative_prompt": "" if kind == "foley" else NEGATIVE}
    required = {"profile", "prompt", "seed", "story"}
    if kind == "foley":
        defaults.update(start_seconds=0, duration=8)
        required.add("source_job_id")
    else:
        defaults.update(width=576, height=1024)
        required.add("image_sha256")
        if kind == "i2v":
            defaults["frames"] = profile["max_frames"]
        else:
            required.update({"audio_sha256", "board_frames"})
    if data.keys() - required - defaults.keys() or required - data.keys():
        raise RequestError("invalid_request", "Request contains missing or unsupported fields")
    if data["profile"] != profile["profile"]:
        raise RequestError("unsupported_profile", "Profile is not supported for this job type")
    request = {**defaults, **data}
    story, seed = request["story"], request["seed"]
    prompt, negative = request["prompt"], request["negative_prompt"]
    limit = 2048 if kind == "foley" else 4096
    max_seed = {"i2v": MAX_SEED, "s2v": MAX_SEED - MAX_CHUNKS + 1, "foley": 2**32 - 1}[kind]
    if not isinstance(story, str) or not STORY.fullmatch(story):
        raise RequestError("invalid_request", "Provide a full OpenProject story URL")
    if type(seed) is not int or not 0 <= seed <= max_seed:
        raise RequestError("invalid_request", "Invalid seed")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > limit:
        raise RequestError("invalid_request", "Invalid prompt")
    if not isinstance(negative, str) or len(negative) > limit or (kind != "foley" and not negative.strip()):
        raise RequestError("invalid_request", "Invalid negative prompt")
    if any(0xD800 <= ord(char) <= 0xDFFF or char == "\0" for text in (prompt, negative) for char in text):
        raise RequestError("invalid_request", "Invalid prompt text")
    request["prompt"] = prompt.strip()
    if kind == "foley":
        source = request["source_job_id"]
        if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{32}", source):
            raise RequestError("invalid_request", "Invalid source video job ID")
        start, duration = request["start_seconds"], request["duration"]
        if (type(start) not in (int, float) or not 0 <= start <= 300
                or type(duration) not in (int, float)
                or not profile["min_duration"] <= duration <= profile["max_duration"]):
            raise RequestError("invalid_request", "Invalid Foley window")
        request.update(start_seconds=float(start) or 0.0, duration=float(duration))
        return request
    for key in ("width", "height"):
        side = request[key]
        if type(side) is not int or not 64 <= side <= 1280 or side % 32:
            raise RequestError("invalid_request", "Dimensions must be multiples of 32 from 64 to 1280")
    for key in ("image_sha256", "audio_sha256") if kind == "s2v" else ("image_sha256",):
        if not isinstance(request[key], str) or not SHA256.fullmatch(request[key]):
            raise RequestError("invalid_request", "Use an uploaded input's SHA-256")
    if kind == "i2v":
        frames = request["frames"]
        if type(frames) is not int or not 5 <= frames <= profile["max_frames"] or frames % 4 != 1:
            raise RequestError("invalid_request", "I2V frames must be 4k + 1 from 5 to 121")
    else:
        frames = request["board_frames"]
        if type(frames) is not int or frames < 1 or s2v_length(frames) > MAX_CHUNKS * CHUNK:
            raise RequestError("invalid_request", "S2V board must fit within three 77-frame chunks")
    return request


def s2v_length(board_frames):
    length = 4 * ((board_frames + 5) // 6) + 1
    return length if length <= CHUNK else CHUNK * ((length + CHUNK - 1) // CHUNK)


def input_path(root, sha256, suffix):
    return root / "input" / "studio-v1" / (sha256 + "." + suffix)


def wav_info(path):
    """Read the exact PCM bit depth and verify the declared samples fit in the file."""
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        try:
            riff, length, kind = struct.unpack("<4sI4s", source.read(12))
            end = length + 8
            if riff != b"RIFF" or kind != b"WAVE" or not 12 <= end <= size <= MAX_UPLOAD:
                raise ValueError("Invalid RIFF header")
            frame_size = frames = 0
            while source.tell() + 8 <= end:
                chunk, length = struct.unpack("<4sI", source.read(8))
                next_chunk = source.tell() + length + (length & 1)
                if next_chunk > end:
                    raise ValueError("Incomplete RIFF chunk")
                if chunk == b"fmt ":
                    if frame_size:
                        raise ValueError("Duplicate WAV format")
                    fmt = source.read(min(length, 40))
                    encoding, channels, rate, _, alignment, bits = struct.unpack_from("<HHIIHH", fmt)
                    # WAVE_FORMAT_EXTENSIBLE names the PCM encoding with a GUID.
                    pcm = encoding == 1 or (
                        encoding == 0xfffe and len(fmt) == 40
                        and struct.unpack_from("<H", fmt, 16)[0] >= 22
                        and struct.unpack_from("<H", fmt, 18)[0] == 16
                        and fmt[24:40] == WAV_PCM_SUBFORMAT
                    )
                    if not pcm or channels not in (1, 2) or rate not in WAV_RATES or bits != 16:
                        raise RequestError("unsupported_wav_format",
                                           "Use 16-bit PCM WAV with one or two channels at "
                                           "16000, 22050, 24000, 44100 or 48000 Hz")
                    frame_size = channels * 2
                    if alignment != frame_size:
                        raise ValueError("Invalid WAV frame alignment")
                if chunk == b"data":
                    if frames:
                        raise ValueError("Duplicate WAV sample data")
                    if not frame_size or not length or length % frame_size:
                        raise ValueError("WAV has missing samples")
                    frames = length // frame_size
                source.seek(next_chunk)
            if not frames or source.tell() != end:
                raise ValueError("WAV has missing samples or an incomplete RIFF chunk")
            return frames, rate
        except RequestError:
            raise
        except (ValueError, struct.error) as exc:
            raise RequestError("invalid_input", "Malformed or incomplete PCM WAV") from exc


def validate_png(path):
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        header = source.read(33)
        if len(header) != 33 or header[:16] != PNG + b"\x00\x00\x00\x0dIHDR":
            raise RequestError("invalid_input", "Invalid PNG header")
        width, height = struct.unpack(">II", header[16:24])
        if not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise RequestError("invalid_input", "PNG dimensions are outside the input limit")
        seen_idat = False
        for _ in range(4096):
            chunk = source.read(8)
            if len(chunk) != 8:
                break
            length, kind = struct.unpack(">I4s", chunk)
            end = source.tell() + length + 4  # Data and CRC.
            if end > size:
                break
            if kind == b"acTL":
                raise RequestError("animated_png", "Use a single-frame PNG")
            if kind == b"IEND":
                if seen_idat and length == 0 and end == size:
                    return width, height
                break
            if kind == b"IDAT":
                seen_idat = True
            source.seek(end)
        raise RequestError("invalid_input", "PNG must contain image data and end at IEND within 4096 chunks after IHDR")


def validate_media(path, suffix):
    if suffix == "wav":
        wav_info(path)
    else:
        validate_png(path)


def check_video_inputs(root, kind, request):
    image = input_path(root, request["image_sha256"], "png")
    if not image.is_file():
        raise RequestError("input_missing", "Upload the PNG first", 404)
    if kind == "s2v":
        audio = input_path(root, request["audio_sha256"], "wav")
        try:
            frames, rate = wav_info(audio)
        except FileNotFoundError as exc:
            raise RequestError("input_missing", "Upload the PCM WAV first", 404) from exc
        exact = request["board_frames"] * rate
        if not exact // 24 <= frames <= (exact + 23) // 24:
            raise RequestError("invalid_input", "WAV samples must match board_frames / 24 seconds")


def check_input_capacity(directory, size):
    # The PVC also holds engines, ledgers and results. Serialize checks and
    # uploads in the API process and include temporary files left by a crash.
    if size > MAX_INPUT_BYTES:
        raise RequestError("input_storage_full", "Staged input quota reached", 507)
    total = size
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, start=1):
            total += entry.stat(follow_symlinks=False).st_size
            if count >= MAX_INPUT_FILES or total > MAX_INPUT_BYTES:
                raise RequestError("input_storage_full", "Staged input quota reached", 507)
    if shutil.disk_usage(directory).free - size < MIN_WORKSPACE_FREE:
        raise RequestError("input_storage_full", "Workspace free-space reserve reached", 507)


def check_input_hash(checksum, expected):
    if checksum.hexdigest() != expected:
        raise RequestError("input_hash_mismatch", "Body SHA-256 differs from the input URL")


def verify_existing_input(path, sha256):
    try:
        with path.open("rb") as existing:
            matches = hashlib.file_digest(existing, "sha256").hexdigest() == sha256
    except OSError:
        matches = False
    if not matches:
        raise RequestError("input_conflict", "Stored input failed SHA-256 verification", 409)


def stage_input(root, sha256, suffix, read, size):
    path = input_path(root, sha256, suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256()
    if path.exists():
        # A replay never needs a temporary file or more persistent space.
        for chunk in read_blocks(read, size):
            checksum.update(chunk)
        check_input_hash(checksum, sha256)
        verify_existing_input(path, sha256)
        return False
    check_input_capacity(path.parent, size)
    with tempfile.NamedTemporaryFile(dir=path.parent) as temp:
        for chunk in read_blocks(read, size):
            checksum.update(chunk)
            temp.write(chunk)
        check_input_hash(checksum, sha256)
        temp.flush()
        validate_media(Path(temp.name), suffix)
        os.fsync(temp.fileno())
        created = publish(Path(temp.name), path)
    if not created:
        verify_existing_input(path, sha256)
    return created


def read_blocks(read, size):
    while size:
        chunk = read(min(size, 1024 * 1024))
        if not chunk:
            raise RequestError("invalid_input", "Upload ended before Content-Length bytes")
        size -= len(chunk)
        yield chunk


def publish(temp, path):
    """Atomically create a destination without replacing an existing file."""
    try:
        os.link(temp, path)
    except FileExistsError:
        return False
    return True


def read_json(handler):
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_JSON or handler.headers.get("Transfer-Encoding"):
            raise ValueError("Invalid length")
        raw = handler.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Incomplete body")
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise RequestError("invalid_request", "Expected bounded JSON with a Content-Length") from exc


def read_job(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def replay(job, kind, request_hash):
    if job is None:
        return False
    if job.get("api_version") == VERSION and job.get("type") == kind and job.get("request_sha256") == request_hash:
        return True
    raise RequestError("id_conflict", "Job ID already belongs to a different request", 409)


def create_job(path, job):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent) as temp:
        json.dump(job, temp)
        temp.flush()
        os.fsync(temp.fileno())
        if not publish(Path(temp.name), path):
            raise RequestError("id_conflict", "Job ID already exists", 409)


def result(path, media_type):
    with path.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise ValueError("Generated file is empty")
        sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    return {"bytes": size, "sha256": sha256, "media_type": media_type}


def job_view(job):
    kind, job_id = job["type"], job["id"]
    status = f"/v1/jobs/{kind}/{job_id}"
    view = {key: job[key] for key in (
        "api_version", "type", "id", "state", "profile", "request_sha256", "story", "seed", "created_at",
        "started_at", "finished_at", "prompt_id", "graph_sha256", "image_sha256", "audio_sha256",
        "width", "height", "frames", "board_frames", "source_job_id", "start_seconds", "duration",
        "poll_misses", "unexpected_work_seen", "error", "admission_code", "telemetry", "provenance",
    ) if key in job}
    view.update(status_url=status, result=None)
    if job["state"] == "succeeded":
        result = job["result"] if kind != "foley" else {
            "bytes": job["bytes"], "sha256": job["provenance"]["output_sha256"], "media_type": "audio/wav",
        }
        view["result"] = {**result, "file_url": status + "/file"}
    return view
