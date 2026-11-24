"""Loopback adapter for the pinned ControlFoley worker in the Wan pod."""
import os
from http.client import HTTPException
from pathlib import Path
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import product
import product_local as local

# Use the sidecar image's own generation, lease, GPU guards and legacy routes.
sys.path.insert(0, "/opt/controlfoley-tools")
import service

CONFIG = Path(os.environ.get("VIDEO_CONFIG", "/config/video"))
acquire_foley = service.acquire


def acquire_with_local():
    lease = acquire_foley()
    if lease is None:
        return None
    try:
        lane = local.enabled_lane(service.ROOT, CONFIG)
        if lane:
            lane.prepare_other({service.CARD}, lease, service.idle)
        return lease
    except (OSError, ValueError, RuntimeError, HTTPException):
        lease.close()
        return None


def submit(job_id, request):
    path = service.job_path(job_id)
    request_hash = product.digest(request)
    with service.LOCK:
        existing = product.read_job(path)
        if product.replay(existing, "foley", request_hash):
            return 200, product.job_view(existing)
        if service.ACTIVE:
            raise product.RequestError("worker_busy", "Foley worker is busy", 409)
        if not (service.WEIGHTS / "manifest.json").is_file():
            raise product.RequestError("worker_unavailable", "Foley models are unavailable", 503)
        try:
            parameters = service.validate(request)
        except (FileNotFoundError, ValueError) as exc:
            raise product.RequestError("invalid_source", "Foley requires a completed source video") from exc
        lease = service.acquire()
        if lease is None:
            raise product.RequestError("worker_busy", "Compute is reserved by another video/audio job", 409)
        started = False
        try:
            try:
                service.idle()
            except RuntimeError as exc:
                raise product.RequestError("worker_busy", "Wan is busy or requires inspection", 409) from exc
            job = {**parameters, "id": job_id, "type": "foley", "api_version": product.VERSION,
                   "profile": request["profile"], "request_sha256": request_hash, "story": request["story"],
                   "kind": "video_conditioned_audio", "state": "queued", "created_at": time.time(),
                   "telemetry": []}
            product.create_job(path, job)
            service.ACTIVE = job_id
            accepted = product.job_view(job)
            try:
                threading.Thread(target=service.generate, args=(job, lease), daemon=True).start()
            except RuntimeError:
                job.update(state="failed", error="Could not start worker", finished_at=time.time())
                service.save(job)
                return 503, product.job_view(job)
            started = True
            return 202, accepted
        finally:
            if not started:
                lease.close()
                if service.ACTIVE == job_id:
                    service.ACTIVE = None


class Handler(service.Handler):
    def do_GET(self):
        if self.path == "/v1/ready":
            if not (service.WEIGHTS / "manifest.json").is_file():
                self.respond(503, {"code": "worker_unavailable", "error": "Foley models are unavailable"})
                return
            self.respond(200, {"api_version": product.VERSION, "active_job": service.ACTIVE})
            return
        super().do_GET()

    def do_PUT(self):
        try:
            route = product.JOB_ROUTE.fullmatch(self.path)
            if not route or route[1] != "foley" or route[3]:
                raise product.RequestError("not_found", "Not found", 404)
            request = product.validate("foley", product.read_json(self))
            status, body = submit(route[2], request)
        except product.RequestError as exc:
            status, body = exc.status, exc.body()
        except Exception:
            status, body = 503, {"code": "worker_unavailable", "error": "Foley worker unavailable"}
        self.respond(status, body)


def recover():
    directory = service.ROOT / "foley-jobs"
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.glob("*.json"):
        job = product.read_job(path)
        if job and job.get("state") in ("queued", "running"):
            job.update(state="failed", error="Worker restarted; not replayed", finished_at=time.time())
            service.save(job)


if __name__ == "__main__":
    recover()
    service.acquire = acquire_with_local
    ThreadingHTTPServer(("127.0.0.1", 8189), Handler).serve_forever()
