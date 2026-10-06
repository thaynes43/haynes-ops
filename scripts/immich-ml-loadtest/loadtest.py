"""Worst-case load test for immich-machine-learning (runbook: .agents/runbooks/immich-ml-backlog.md).

It POSTs straight to ML's /predict, so Immich's database and job queues are never touched.
The request bodies match immich-server v3.2.4
(server/src/repositories/machine-learning.repository.ts:190-242, defaults from
server/src/dtos/config.dto.ts:619-650). While the load runs, it times voice requests to the
GPU neighbours (ollama-assist02's /api/chat, and Wyoming STT on whisper with a clip that
kokoro synthesizes at start-up), so their latency can be compared with and without the load.

Inputs:
  * real preview JPEGs listed in PICKS as "<kind> <score> <path>" lines, where kind is
    faces / ocr / wide. render-job.sh picks them read-only from the database (picks.sql).
  * synthetic worst cases generated here: text panoramas 10:1 in both orientations, one very
    long text line (the widest OCR recognition crop), a dense text page, a 1:30 scrolling
    screenshot, and a 2x2 tile of the most face-heavy preview (four times its faces in one
    request).

PLAN is "name:seconds:clip:faces:ocr,..." with the number of concurrent workers per model
type. Phases with 0/0/0 run the voice probes alone. Output lines (grep for them in the Job
log):
  P,<phase>,<probe>,<seconds>,<ok>,<detail>   one voice probe
  S,<phase>,<kind>,n=..,ok=..,err=..,p50=..,p95=..,max=..   per phase and model type
  E,<phase>,<kind>,<status>,<body>   the first few errors of each phase and type
"""

import io
import json
import os
import random
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ML_URL = os.environ.get("ML_URL", "http://immich-machine-learning.photos.svc.cluster.local:3003").rstrip("/")
PLAN = os.environ.get("PLAN", "baseline:120:0:0:0,default:300:2:2:1,heavy:300:4:4:4,recovery:120:0:0:0")
PICKS = os.environ.get("PICKS", "")  # "<kind> <score> <path>" lines
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-assist02.ai.svc.cluster.local:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
WHISPER = os.environ.get("WHISPER", "whisper.ai.svc.cluster.local:10300")
KOKORO = os.environ.get("KOKORO", "kokoro.ai.svc.cluster.local:10210")
PROBE_INTERVAL_S = float(os.environ.get("PROBE_INTERVAL_S", "15"))
REQUEST_TIMEOUT_S = float(os.environ.get("REQUEST_TIMEOUT_S", "300"))  # undici's default headersTimeout

CLIP = {"clip": {"visual": {"modelName": "ViT-B-32__openai"}}}
FACES = {
    "facial-recognition": {
        "detection": {"modelName": "buffalo_l", "options": {"minScore": 0.7}},
        "recognition": {"modelName": "buffalo_l"},
    }
}
OCR = {
    "ocr": {
        "detection": {"modelName": "PP-OCRv5_mobile", "options": {"minScore": 0.5, "maxResolution": 736}},
        "recognition": {"modelName": "PP-OCRv5_mobile", "options": {"minScore": 0.8}},
    }
}

phase = "setup"
lock = threading.Lock()
stats: dict[tuple[str, str], list[tuple[float, int]]] = {}
errors_logged: dict[tuple[str, str], int] = {}


def log(line: str) -> None:
    print(line, flush=True)


# ---------------------------------------------------------------- images


def jpeg(img: Image.Image, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def font(size: int) -> ImageFont.ImageFont:
    for path in ("/opt/venv/lib/python3.11/site-packages/cv2/qt/fonts/DejaVuSans.ttf",):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


WORDS = (
    "kitchen garage living room bedroom porch thermostat camera driveway mailbox birthday "
    "vacation receipt invoice menu ticket boarding pass passport street sign parking exit "
    "warning caution 2026 total amount due 14:35 September October lights fan door window"
).split()


def text_image(width: int, height: int, size: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (width, height), (245, 245, 240))
    draw = ImageDraw.Draw(img)
    f = font(size)
    y = size // 2
    while y < height - size:
        x = size // 2
        while x < width - size * 4:
            word = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 4)))
            draw.text((x, y), word, fill=(rnd.randint(0, 60),) * 3, font=f)
            x += int(draw.textlength(word, font=f)) + size * rnd.randint(1, 3)
        y += int(size * 1.6)
    return img


def long_line_image() -> Image.Image:
    # One line of text across a 10:1 preview: after detection it is one very wide crop, the
    # widest OCR recognition input (48 x thousands of pixels).
    img = Image.new("RGB", (14400, 1440), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    f = font(64)
    line = " ".join(WORDS * 6)
    draw.text((40, 680), line[:520], fill=(10, 10, 10), font=f)
    return img


def load_picks() -> dict[str, list[bytes]]:
    picks: dict[str, list[bytes]] = {"faces": [], "ocr": [], "wide": []}
    face_top: tuple[float, bytes] | None = None
    for raw in PICKS.splitlines():
        parts = raw.split(maxsplit=2)
        if len(parts) != 3 or parts[0] not in picks:
            continue
        try:
            with open(parts[2].strip(), "rb") as img_fh:
                data = img_fh.read()
        except OSError as error:
            log(f"W,setup,missing pick {error}")
            continue
        picks[parts[0]].append(data)
        if parts[0] == "faces" and (face_top is None or float(parts[1]) > face_top[0]):
            face_top = (float(parts[1]), data)
    synthetic_ocr = [
        jpeg(text_image(14400, 1440, 44, 1)),  # 10:1 panorama full of text
        jpeg(text_image(1440, 14400, 44, 2)),  # 1:10
        jpeg(text_image(1440, 1920, 22, 3)),  # a dense page of small text
        jpeg(long_line_image()),
        # A long phone scrolling screenshot (1:30). Its preview keeps the 1170 px short side
        # (fit outside 1440, without enlargement), so OCR detection sees 736 x ~22,000.
        jpeg(text_image(1170, 35100, 28, 4)),
    ]
    picks["ocr"] += synthetic_ocr
    if face_top is not None:
        top = Image.open(io.BytesIO(face_top[1]))
        w, h = top.size
        tile = Image.new("RGB", (w * 2, h * 2))
        for dx in (0, w):
            for dy in (0, h):
                tile.paste(top, (dx, dy))
        picks["faces"].append(jpeg(tile))
    for kind, items in picks.items():
        log(f"I,setup,{kind} images={len(items)} bytes={sum(map(len, items))}")
    return picks


# ---------------------------------------------------------------- ML requests


def multipart(entries: dict, image: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="entries"\r\n\r\n'.encode())
    body.write(json.dumps(entries).encode())
    body.write(
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="blob"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    body.write(image)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def predict(entries: dict, image: bytes) -> tuple[int, str]:
    data, ctype = multipart(entries, image)
    req = urllib.request.Request(f"{ML_URL}/predict", data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            resp.read()
            return resp.status, ""
    except urllib.error.HTTPError as error:
        return error.code, error.read()[:300].decode(errors="replace")
    except Exception as error:  # timeouts, resets
        return 0, repr(error)[:300]


def worker(kind: str, entries: dict, images: list[bytes], my_phase: str, stop: threading.Event) -> None:
    rnd = random.Random(f"{kind}{my_phase}{threading.get_ident()}")
    while not stop.is_set():
        image = rnd.choice(images)
        start = time.monotonic()
        status, body = predict(entries, image)
        elapsed = time.monotonic() - start
        with lock:
            stats.setdefault((my_phase, kind), []).append((elapsed, status))
            if status != 200:
                key = (my_phase, kind)
                errors_logged[key] = errors_logged.get(key, 0) + 1
                if errors_logged[key] <= 3:
                    log(f"E,{my_phase},{kind},{status},{body!r}")


# ---------------------------------------------------------------- voice probes


def ollama_probe() -> tuple[bool, str]:
    system = (
        "You are a voice assistant for a smart home. Answer in one short sentence. Devices: "
        + ", ".join(f"light.room_{i} (on)" for i in range(120))
    )
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Is light.room_{random.randint(0, 119)} on? Answer in one sentence that names it."},
        ],
        "stream": False,
        "think": False,
        "options": {"num_predict": 24, "temperature": 0, "seed": 7},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read())
    ms = lambda key: round(out.get(key, 0) / 1e6)  # noqa: E731
    return True, f"prompt_ms={ms('prompt_eval_duration')} eval_ms={ms('eval_duration')} tokens={out.get('eval_count')}"


def wy_send(sock: socket.socket, etype: str, data: dict | None = None, payload: bytes = b"") -> None:
    header: dict = {"type": etype, "version": "1.5.0"}
    body = json.dumps(data).encode() if data else b""
    if body:
        header["data_length"] = len(body)
    if payload:
        header["payload_length"] = len(payload)
    sock.sendall(json.dumps(header).encode() + b"\n" + body + payload)


def wy_recv(fh) -> tuple[str, dict, bytes]:
    line = fh.readline()
    if not line:
        raise ConnectionError("wyoming peer closed")
    header = json.loads(line)
    data = header.get("data") or {}
    if header.get("data_length"):
        data.update(json.loads(fh.read(header["data_length"])))
    payload = fh.read(header["payload_length"]) if header.get("payload_length") else b""
    return header["type"], data, payload


def wy_connect(hostport: str) -> socket.socket:
    host, port = hostport.rsplit(":", 1)
    return socket.create_connection((host, int(port)), timeout=60)


def synthesize_clip() -> tuple[bytes, int] | None:
    try:
        with wy_connect(KOKORO) as sock, sock.makefile("rb") as fh:
            wy_send(sock, "synthesize", {"text": "Turn on the kitchen lights and set the thermostat to seventy two degrees."})
            audio, rate, width, channels = b"", 24000, 2, 1
            while True:
                etype, data, payload = wy_recv(fh)
                if etype == "audio-start":
                    rate, width, channels = data.get("rate", rate), data.get("width", width), data.get("channels", channels)
                elif etype == "audio-chunk":
                    audio += payload
                elif etype == "audio-stop":
                    break
        samples = np.frombuffer(audio, dtype=np.int16).reshape(-1, channels)[:, 0].astype(np.float32)
        if rate != 16000:  # Wyoming STT services expect 16 kHz mono 16-bit
            idx = np.arange(0, len(samples), rate / 16000)
            samples = np.interp(idx, np.arange(len(samples)), samples)
        pcm = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
        log(f"I,setup,stt clip {len(pcm) / 32000:.1f}s from kokoro (rate {rate})")
        return pcm, 16000
    except Exception as error:
        log(f"W,setup,kokoro synth failed, no STT probe: {error!r}")
        return None


def whisper_probe(clip: bytes) -> tuple[bool, str]:
    fmt = {"rate": 16000, "width": 2, "channels": 1}
    with wy_connect(WHISPER) as sock, sock.makefile("rb") as fh:
        wy_send(sock, "transcribe", {"language": "en"})
        wy_send(sock, "audio-start", fmt)
        for i in range(0, len(clip), 3200):  # 100 ms chunks, sent as fast as possible
            wy_send(sock, "audio-chunk", fmt, clip[i : i + 3200])
        stop_at = time.monotonic()
        wy_send(sock, "audio-stop")
        while True:
            etype, data, _ = wy_recv(fh)
            if etype == "transcript":
                after_stop = time.monotonic() - stop_at
                return True, f"after_stop_s={after_stop:.3f} text={data.get('text', '')[:60]!r}"


def probes(stop: threading.Event, clip: tuple[bytes, int] | None) -> None:
    while not stop.is_set():
        for name, fn in (("ollama", ollama_probe), ("whisper", (lambda: whisper_probe(clip[0])) if clip else None)):
            if fn is None or stop.is_set():
                continue
            start = time.monotonic()
            try:
                ok, detail = fn()
            except Exception as error:
                ok, detail = False, repr(error)[:200]
            log(f"P,{phase},{name},{time.monotonic() - start:.3f},{int(ok)},{detail}")
        stop.wait(PROBE_INTERVAL_S)


# ---------------------------------------------------------------- main


def summary(name: str) -> None:
    for (p, kind), rows in sorted(stats.items()):
        if p != name:
            continue
        times = sorted(t for t, _ in rows)
        ok = sum(1 for _, s in rows if s == 200)
        codes: dict[int, int] = {}
        for _, s in rows:
            if s != 200:
                codes[s] = codes.get(s, 0) + 1
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        log(
            f"S,{p},{kind},n={len(rows)},ok={ok},err={len(rows) - ok},errcodes={codes},"
            f"p50={statistics.median(times):.2f},p95={p95:.2f},max={times[-1]:.2f}"
        )


def main() -> int:
    global phase
    picks = load_picks()
    clip_images = picks["faces"] + picks["ocr"] + picks["wide"]
    clip = synthesize_clip()
    probe_stop = threading.Event()
    threading.Thread(target=probes, args=(probe_stop, clip), daemon=True).start()
    for step in PLAN.split(","):
        name, seconds, n_clip, n_faces, n_ocr = step.split(":")
        phase = name
        log(f"I,{name},start seconds={seconds} clip={n_clip} faces={n_faces} ocr={n_ocr} t={time.time():.0f}")
        stop = threading.Event()
        threads = []
        for kind, entries, images, count in (
            ("clip", CLIP, clip_images, int(n_clip)),
            ("faces", FACES, picks["faces"] + picks["wide"], int(n_faces)),
            ("ocr", OCR, picks["ocr"] + picks["wide"], int(n_ocr)),
        ):
            for _ in range(count):
                t = threading.Thread(target=worker, args=(kind, entries, images, name, stop), daemon=True)
                t.start()
                threads.append(t)
        time.sleep(float(seconds))
        stop.set()
        for t in threads:
            t.join(timeout=REQUEST_TIMEOUT_S)
        log(f"I,{name},end t={time.time():.0f}")
        summary(name)
    probe_stop.set()
    errors = sum(1 for rows in stats.values() for _, s in rows if s != 200)
    log(f"I,done,ml_errors={errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
