"""Verify the two private archive deliveries through the internal TLS ingress."""

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import socket
import ssl


HOST = "studio.haynesops.com"
INGRESS = "traefik-internal.network.svc.cluster.local"
KEY_DIRECTORY = Path("/run/studio-playback")


@dataclass(frozen=True)
class Film:
    name: str
    archive: str
    source_kind: str
    production: str
    story: str
    run: str
    delivery: str
    master: tuple[int, str]
    proxy: tuple[int, str]


FILMS = (
    Film(
        "one-more-meeting",
        "one-more-meeting-e1-942",
        "canonical",
        "prd_01M7ZGYNHFET1GKR4304FVD4PE",
        "sty_01M7ZGYNHFET1GKR4304FVD4PF",
        "run_01M7ZHNZJ2PWA8XR8K7KKWP17V",
        "dlv_01M7ZHP1QS82QGK196ZPCM7MQS",
        (8242309, "557a45be75bf29aa384c74405faeaadf53080f619fca8c77f7f5e907227f74d6"),
        (1789344, "63dbb1e26287360ed6c4a0422dbdbfa250ad981c74e9719133d4dfe72d809d1b"),
    ),
    Film(
        "winter-menu",
        "the-winter-menu-e1-quieter-gavel-1",
        "recovered",
        "prd_01M79V9FC00000000000000001",
        "sty_01M79V9FC00000000000000001",
        "run_01M7C5CJA7XKBCRPM0ZRP2P5RS",
        "dlv_01M7C5D3KFX3MFVWCXBCVJHMG7",
        (60560455, "92d1efa3a30c4de10c62298cd25c20671f729ab1acf257ad7cdfaecd9edb09bc"),
        (12986556, "0212a7de9d6b7816c4c4d3c98bfd6d8e80fc112163944152e399b3e980a6748d"),
    ),
)


class IngressConnection(HTTPSConnection):
    def __init__(self):
        self.tls = ssl.create_default_context()
        super().__init__(HOST, timeout=10, context=self.tls)

    def connect(self):
        # Dial the internal Service. Keep the route host for SNI, certificate
        # hostname validation and the HTTP Host header.
        transport = socket.create_connection((INGRESS, self.port), self.timeout)
        try:
            self.sock = self.tls.wrap_socket(transport, server_hostname=HOST)
        except OSError:
            transport.close()
            raise


class ProofFailure(Exception):
    """A check failed. Messages use labels and measured status only."""


def require(condition, step):
    if not condition:
        raise ProofFailure(step)


def request(conn, method, path, key, *, headers=None, body=None):
    headers = {"Accept-Encoding": "identity", **(headers or {})}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    conn.request(method, path, body, headers)
    return conn.getresponse()


def read_json(response, step):
    require(response.getheader("Content-Type") == "application/json", f"{step}: content type")
    data = response.read(128 * 1024 + 1)
    require(len(data) <= 128 * 1024, f"{step}: response size")
    return json.loads(data)


def api_read(conn, path, key, step):
    with request(conn, "GET", path, key) as response:
        require(response.status == 200, f"{step}: HTTP {response.status}")
        return read_json(response, step)


def transfer(conn, path, key, total, start, end, *, range_header=None, method="GET", keep=False):
    expected_status = 206 if range_header else 200
    content_range = f"bytes {start}-{end}/{total}" if range_header else None
    count = end - start + 1
    step = f"{method} media {range_header or 'unranged'}"
    headers = {"Range": range_header} if range_header else None
    digest = sha256()
    content = bytearray()
    received = 0
    with request(conn, method, path, key, headers=headers) as response:
        require(response.status == expected_status, f"{step}: HTTP {response.status}")
        for header, expected in (
            ("Accept-Ranges", "bytes"),
            ("Content-Range", content_range),
            ("Content-Length", str(count)),
            ("Content-Type", "video/mp4"),
            ("Content-Encoding", None),
        ):
            require(response.getheader(header) == expected, f"{step}: {header}")
        for chunk in iter(lambda: response.read(64 * 1024), b""):
            received += len(chunk)
            require(received <= count, f"{step}: body too long")
            digest.update(chunk)
            if keep:
                content.extend(chunk)
        require(received == (0 if method == "HEAD" else count), f"{step}: body size")
    result = {"status": expected_status, "bytes": received, "content_length": count}
    if range_header:
        result["content_range"] = content_range
    if method == "GET":
        result["sha256"] = digest.hexdigest()
    return result, content


def verify_denial(conn, method, path, key, status_codes, *, headers=None, body=None):
    with request(conn, method, path, key, headers=headers, body=body) as response:
        require(response.status in status_codes, f"{method} refusal: HTTP {response.status}")
        require(response.getheader("Content-Range") is None, f"{method} refusal: Content-Range")
        require(response.getheader("Content-Type") != "video/mp4", f"{method} refusal: media")
        if method == "HEAD":
            require(not response.read(1), "HEAD refusal: body size")
        else:
            error = read_json(response, f"{method} refusal")["error"]
            require(error["code"] == status_codes[response.status], f"{method} refusal: error code")
        return response.status


def verify_film(conn, film, key, other_key):
    listed = api_read(conn, "/v1/productions", key, "production listing")
    require({entry["id"] for entry in listed["items"]} == {film.production}
            and listed["next_cursor"] is None, "production listing: scope")

    run_path = f"/v1/runs/{film.run}"
    run = api_read(conn, run_path, key, "run")
    marker = (run["legacy"] or {}).get("archive_import", {})
    require(run["active_approval_id"] is None and marker.get("source_directory") == film.archive
            and marker.get("source_kind") == film.source_kind, "run: archive import marker")
    delivery = api_read(conn, run_path + "/delivery", key, "delivery")
    require(delivery["id"] == film.delivery and delivery["run_id"] == film.run, "delivery identity")
    for role, expected in (("master", film.master), ("proxy", film.proxy)):
        require((delivery[role]["bytes"], delivery[role]["sha256"]) == expected, f"delivery {role} identity")

    master_size, master_hash = delivery["master"]["bytes"], delivery["master"]["sha256"]
    master, _ = transfer(conn, f"/v1/media/{master_hash}", key, master_size, 0, master_size - 1)
    require(master["sha256"] == master_hash, "master hash")

    size, proxy_hash = delivery["proxy"]["bytes"], delivery["proxy"]["sha256"]
    path = f"/v1/media/{proxy_hash}"
    head, _ = transfer(conn, path, key, size, 0, size - 1, method="HEAD")
    full, proxy = transfer(conn, path, key, size, 0, size - 1, range_header="bytes=0-", keep=True)
    require(full["sha256"] == proxy_hash, "whole proxy hash")

    past_middle = size // 2 + 1
    ranges = (
        ("bytes=0-65535", 0, 65535),
        (f"bytes={past_middle}-{past_middle + 65535}", past_middle, past_middle + 65535),
        ("bytes=-65536", size - 65536, size - 1),
    )
    samples = []
    for header, start, end in ranges:
        result, content = transfer(conn, path, key, size, start, end, range_header=header, keep=True)
        require(content == proxy[start:end + 1], f"{header}: bytes differ from verified proxy")
        samples.append(result)

    other = {404: "not_found"}
    anonymous = {401: "auth.required"}
    unknown_status = verify_denial(conn, "GET", "/v1/media/" + "0" * 64, other_key, other)
    refused = []
    requests = [("HEAD", None), ("GET", "bytes=0-")]
    requests.extend(("GET", header) for header, _, _ in ranges)
    for method, header in requests:
        headers = {"Range": header} if header else None
        refused.append({
            "method": method, "range": header,
            "other_production": verify_denial(conn, method, path, other_key, other, headers=headers),
            "anonymous": verify_denial(conn, method, path, None, anonymous, headers=headers),
        })

    approval = verify_denial(
        conn, "POST", run_path + "/go-live-approvals", key,
        # An agent key must be stopped by the people-only gate before the handler
        # runs. Accepting the legacy refusal too would hide a regression that let a
        # key reach the handler, because an imported run is refused there anyway.
        {403: "approval.approver_not_permitted"},
        body={"master_sha256": master_hash, "platform_setting": True},
    )
    story_path = f"/v1/stories/{film.story}"
    story = api_read(conn, story_path, key, "story")
    # A valid but stale revision also prevents draft or queue changes if the
    # permission gate regresses. A conflict is still a failing proof.
    stale_revision = 1 if story["revision"] != 1 else 2**63 - 2
    headers = {"If-Match": f'"{stale_revision}"'}
    forbidden = {403: "auth.forbidden"}
    draft = verify_denial(conn, "PATCH", story_path, key, forbidden,
                          headers=headers, body={"title": story["title"]})
    queue = verify_denial(conn, "POST", story_path + "/runs", key, forbidden,
                          headers={**headers, "Idempotency-Key": "op957-playback-proof"})
    return {
        "productions_status": 200, "run_status": 200, "delivery_status": 200, "master": master,
        "proxy_head": head, "proxy_full": full, "proxy_ranges": samples,
        "unknown_hash_status": unknown_status, "proxy_refusals": refused,
        "approval_status": approval, "draft_status": draft, "queue_status": queue,
    }


def main():
    evidence = {}
    try:
        keys = [(KEY_DIRECTORY / film.name).read_text(encoding="ascii").rstrip("\r\n") for film in FILMS]
        with closing(IngressConnection()) as conn:
            for film, key, other_key in zip(FILMS, keys, reversed(keys), strict=True):
                evidence[film.name] = verify_film(conn, film, key, other_key)
    except ProofFailure as error:
        raise SystemExit(f"playback proof failed: {error}") from None
    except (HTTPException, OSError, ValueError, TypeError, KeyError) as error:
        raise SystemExit(f"playback proof failed ({type(error).__name__})") from None
    print(json.dumps(evidence, separators=(",", ":")))


if __name__ == "__main__":
    main()
