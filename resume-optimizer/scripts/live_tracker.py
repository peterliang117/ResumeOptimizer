"""Serve the local application tracker with automatic browser refresh."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from tracker import upsert_tracker
from tracker_report import read_tracker, render


def tracker_version(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def build_handler(tracker: Path, output: Path) -> type[BaseHTTPRequestHandler]:
    class TrackerHandler(BaseHTTPRequestHandler):
        def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            request_path = urlparse(self.path).path
            if request_path == "/api/tracker-version":
                payload = json.dumps({"version": tracker_version(tracker)}).encode("utf-8")
                self.send_bytes(payload, "application/json; charset=utf-8")
                return

            if request_path in {"/", "/application_tracker_summary.html"}:
                content = render(read_tracker(tracker), tracker)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content, encoding="utf-8")
                self.send_bytes(content.encode("utf-8"), "text/html; charset=utf-8")
                return

            self.send_bytes(b"Not found", "text/plain; charset=utf-8", status=404)

        def do_POST(self) -> None:
            request_path = urlparse(self.path).path
            if request_path != "/api/application-update":
                self.send_bytes(b"Not found", "text/plain; charset=utf-8", status=404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                self.send_bytes(b"Invalid JSON", "text/plain; charset=utf-8", status=400)
                return

            if not payload.get("company") or not payload.get("role"):
                self.send_bytes(b"Company and role are required", "text/plain; charset=utf-8", status=400)
                return

            allowed = {
                "company",
                "role",
                "url",
                "status",
                "stage",
                "stage_date",
                "follow_up_date",
                "next_action",
                "contact_name",
            }
            upsert_tracker(tracker, {key: str(value) for key, value in payload.items() if key in allowed})
            self.send_bytes(b'{"ok":true}', "application/json; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            return

    return TrackerHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a live local tracker dashboard.")
    parser.add_argument("--tracker", type=Path, default=Path("tracker/applications.csv"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/application_tracker_summary.html"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not args.tracker.exists():
        raise SystemExit(f"Tracker not found: {args.tracker}")

    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(args.tracker.resolve(), args.output.resolve()),
    )
    print(f"Live tracker: http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
