"""Make anonymous GETs through Studio's internal ingress, retaining TLS hostname verification."""

import http.client
import re
import socket
import ssl
import sys
from html.parser import HTMLParser


HOST = "studio.haynesops.com"
INGRESS = "traefik-internal.network.svc.cluster.local"
FORMAT_MARKERS = (b"story-format-section", b"Format version", b"Format settings saved.")


def get(path, limit=0):
    with socket.create_connection((INGRESS, 443), timeout=10) as connection:
        with ssl.create_default_context().wrap_socket(connection, server_hostname=HOST) as tls:
            tls.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            with http.client.HTTPResponse(tls) as response:
                response.begin()
                body = response.read(limit + 1) if limit else b""
                if len(body) > limit:
                    raise ValueError(f"response too large at {path}")
                return response.status, response.headers.get_content_type(), body


class EntryModule(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        settings = dict(attrs)
        path = settings.get("src") or ""
        if settings.get("type") == "module" and re.fullmatch(r"/assets/index-[\w-]+\.js", path, re.ASCII):
            self.paths.append(path)


def check_format():
    status, content_type, body = get("/", limit=128 * 1024)
    if status != 200 or content_type != "text/html":
        raise ValueError(f"expected HTML at /, got {status} {content_type}")
    entry = EntryModule()
    entry.feed(body.decode("utf-8"))
    entry.close()
    if len(entry.paths) != 1:
        raise ValueError("expected one Vite entry module at /assets/index-<hash>.js")
    path = entry.paths[0]
    status, content_type, body = get(path, limit=8 * 1024 * 1024)
    if status != 200 or content_type not in {"text/javascript", "application/javascript"}:
        raise ValueError(f"expected JavaScript at {path}, got {status} {content_type}")
    missing = [marker.decode("ascii") for marker in FORMAT_MARKERS if marker not in body]
    if missing:
        raise ValueError(f"missing Format markers at {path}: {', '.join(missing)}")
    print(f"HTTP 200 {path}: matched story-format-section, Format version and Format settings saved.")


def main(mode):
    try:
        if mode == "format":
            check_format()
        elif mode in {"/", "/v1/me"}:
            print(get(mode)[0])
        else:
            raise ValueError("unknown ingress probe")
    except (ValueError, OSError, http.client.HTTPException) as error:
        raise SystemExit(f"Studio ingress probe failed: {error}") from None


if __name__ == "__main__":
    main(sys.argv[1])
