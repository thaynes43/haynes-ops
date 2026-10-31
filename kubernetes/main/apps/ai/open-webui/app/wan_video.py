"""Open WebUI Pipelines adapter for the bounded dual-3090 video API."""
import json
import time
from urllib.request import Request, urlopen

API = "http://wan-video.ai.svc.cluster.local:8080"
PUBLIC = "https://video.haynesops.com"


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(API + path, data=data, headers={"Content-Type": "application/json"}), timeout=30) as response:
        return json.load(response)


class Pipeline:
    def __init__(self):
        self.name = "Wan Video — Dual RTX 3090"

    def pipe(self, user_message: str, model_id: str, messages: list[dict], body: dict):
        # Internal title/tag tasks must never create extra GPU jobs.
        if body.get("task") or (body.get("metadata") or {}).get("task"):
            yield "Wan video"
            return
        if "### Task:" in user_message:
            yield "Wan video"
            return
        try:
            job = request("/videos", {"prompt": user_message, "preset": "preview"})
            job_id = job["id"]
            yield f"Generating your video on both RTX 3090s. [Job status]({PUBLIC}/videos/{job_id})\n\n"
            for _ in range(245):
                result = request("/videos/" + job_id)
                if result["state"] == "succeeded":
                    yield f"[Play or download your video]({PUBLIC}/videos/{job_id}/file)"
                    return
                if result["state"] == "failed":
                    yield "The video job failed. Its status link contains the failure and GPU telemetry."
                    return
                # Keep a streaming response alive without filling the conversation with progress text.
                yield " "
                time.sleep(5)
            yield "The job is still pending. Use its status link; do not resubmit it."
        except Exception:
            yield "The video worker is unavailable or busy. Check the job status before retrying."
