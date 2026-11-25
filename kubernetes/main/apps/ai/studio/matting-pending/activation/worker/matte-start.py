"""Wait for the matte lane grant with a request that cannot take a claim."""

import os
import sys
from pathlib import Path
from time import sleep

import httpx

from haynes_studio.queue.progress import ProgressFile
from haynes_studio.queue.worker_tokens import worker_bearer


def main():
    progress = ProgressFile(os.environ["STUDIO_WORKER_PROGRESS_FILE"])
    secret = Path("/run/secrets/studio-worker/token").read_text().strip()
    token = worker_bearer(secret)
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=os.environ["STUDIO_API_URL"], headers=headers,
                      timeout=5, trust_env=False) as client:
        while True:
            progress.write_status("Waiting for the matte worker lane grant")
            try:
                # A known lane is checked before validation. An extra field
                # makes this payload invalid even after the grant is active.
                response = client.post("/internal/v1/work/claim",
                                       json={"lane": "matte", "registration_probe": True})
                if response.status_code == 422:
                    error = response.json().get("error", {})
                    if error.get("code") == "schema.unknown_field" and error.get("path") == "/registration_probe":
                        break
            except (httpx.RequestError, ValueError):
                pass
            sleep(5)
    # The CLI consumes the bearer from its process environment. The token is
    # projected only into this container, which has its own PID namespace.
    os.execvpe(sys.argv[1], sys.argv[1:], {**os.environ, "STUDIO_WORKER_TOKEN": secret})


if __name__ == "__main__":
    main()
