#!/usr/bin/env python3
"""proxy.py: the naive agent-traffic proxy for Lab 4.

A reverse proxy that prints every request and response and forwards them
unchanged. Standard library only; nothing to install.

    python3 proxy.py                       # listens on http://localhost:8041
    ANTHROPIC_BASE_URL=http://localhost:8041 claude

Everything Claude Code sends to the API now passes through this program and is
printed to the terminal. The proxy changes nothing: headers go through untouched
(a subscription login needs its bearer token and its `anthropic-beta` header),
and the reply is streamed back byte for byte as it arrives.

    python3 proxy.py --port 9000
    python3 proxy.py --upstream https://chatgpt.com/backend-api/codex   # for Codex
"""

from __future__ import annotations

import argparse
import http.client
import json
import itertools
import os
import queue
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# Headers that describe one hop of the connection, not the message. They are
# never forwarded. `accept-encoding` is dropped too, so the API answers in plain
# text and what we print is readable.
SKIP = {
    "host", "connection", "keep-alive", "transfer-encoding", "te", "trailers",
    "upgrade", "proxy-authorization", "proxy-authenticate", "accept-encoding",
    "content-length",
}

UPSTREAM = "https://api.anthropic.com"
HISTORY = 1000
HERE = os.path.dirname(os.path.abspath(__file__))


class Hub:
    """In-memory event history + per-browser queues. Never blocks the relay."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.order: list[int] = []
        self.dropped = 0
        self.next_id = 1
        self.next_ex = 1
        self.subs: list[queue.Queue] = []

    def new_exchange(self) -> int:
        with self.lock:
            n = self.next_ex
            self.next_ex += 1
            return n

    def publish(self, ex: int, type_: str, **data: object) -> None:
        try:
            with self.lock:
                ev = {"id": self.next_id, "ex": ex, "type": type_, "ts": time.time(), **data}
                self.next_id += 1
                if ex not in self.order:
                    self.order.append(ex)
                    while len(self.order) > HISTORY:
                        old = self.order.pop(0)
                        self.dropped += 1
                        self.events = [e for e in self.events if e["ex"] != old]
                self.events.append(ev)
                for q in self.subs:
                    try:
                        q.put_nowait(ev)
                    except queue.Full:
                        pass
        except Exception:
            pass  # observer must never break the relay

    def subscribe(self, last_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=20000)
        with self.lock:
            q.put_nowait({"id": 0, "type": "hello", "dropped": self.dropped})
            for e in self.events:
                if e["id"] > last_id:
                    q.put_nowait(e)
            self.subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def clear(self) -> None:
        with self.lock:
            self.events = []
            self.order = []
            self.dropped = 0
            for q in self.subs:
                try:
                    q.put_nowait({"id": 0, "type": "cleared"})
                except queue.Full:
                    pass


HUB = Hub()


def mask(name: str, value: str) -> str:
    return value[:16] + "..." if name.lower() in ("authorization", "x-api-key") else value


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Tap(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # we print our own lines

    def do_POST(self) -> None:
        if self.path.startswith("/__dashboard"):
            return self.dashboard()
        self.relay()

    def do_GET(self) -> None:
        if self.path.startswith("/__dashboard"):
            return self.dashboard()
        self.relay()

    def dashboard(self) -> None:
        path = self.path.split("?")[0]
        if path == "/__dashboard/clear" and self.command == "POST":
            n = int(self.headers.get("content-length") or 0)
            self.rfile.read(n)
            HUB.clear()
            return self.simple(200, b"ok", "text/plain")
        if path == "/__dashboard/events":
            last = self.headers.get("Last-Event-ID") or "0"
            last = int(last) if last.isdigit() else 0
            q = HUB.subscribe(last)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                        idl = f"id: {ev['id']}\n" if ev["id"] else ""
                        self.wfile.write(f"{idl}data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                HUB.unsubscribe(q)
                self.close_connection = True
            return
        if path in ("/__dashboard", "/__dashboard/"):
            try:
                with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
                    return self.simple(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self.simple(500, b"dashboard.html missing", "text/plain")
        self.simple(404, b"not found", "text/plain")

    def simple(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_HEAD(self) -> None:
        self.relay()

    def relay(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""

        ex = HUB.new_exchange()
        t0 = time.time()
        parsed = None
        if body:
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
        HUB.publish(ex, "request", method=self.command, path=self.path,
                    headers={k: mask(k, v) for k, v in self.headers.items()},
                    body=parsed, body_text=None if parsed is not None else body.decode("utf-8", "replace"))

        # 1. Print what the agent sent.
        print(f"\n{'=' * 78}\n[{stamp()}] >>> {self.command} {self.path}", flush=True)
        for name, value in self.headers.items():
            print(f"    {name}: {mask(name, value)}")
        if body:
            try:
                print(json.dumps(json.loads(body), indent=2, ensure_ascii=False))
            except ValueError:
                print(body.decode("utf-8", "replace"))

        # 2. Forward it, headers intact.
        target = urlsplit(UPSTREAM)
        conn_cls = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        # Connect with a short timeout (a lost SYN must not stall the agent for
        # minutes); the long timeout is for reading a slow stream.
        upstream = conn_cls(target.netloc, timeout=10)
        try:
            upstream.connect()
        except OSError as e:
            print(f"\n[{stamp()}] !!! cannot reach {UPSTREAM}: {e}", flush=True)
            HUB.publish(ex, "error", message=f"cannot reach {UPSTREAM}: {e}", status=502)
            self.send_error(502, f"proxy: cannot reach {UPSTREAM}: {e}")
            self.close_connection = True
            return
        upstream.sock.settimeout(600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP}
        try:
            upstream.request(self.command, target.path.rstrip("/") + self.path, body=body or None, headers=headers)
            reply = upstream.getresponse()
        except OSError as e:
            HUB.publish(ex, "error", message=f"upstream failed: {e}", status=502)
            self.send_error(502, f"proxy: upstream failed: {e}")
            self.close_connection = True
            return
        HUB.publish(ex, "response_start", status=reply.status, headers=dict(reply.getheaders()))

        # 3. Stream the reply back as it arrives, printing it on the way.
        print(f"\n[{stamp()}] <<< {reply.status} {reply.reason}", flush=True)
        self.send_response(reply.status, reply.reason)
        for name, value in reply.getheaders():
            if name.lower() not in SKIP and name.lower() != "content-encoding":
                self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command == "HEAD":
            self.close_connection = True
            return
        size = 0
        try:
            while chunk := reply.read1(4096):  # read1: pass on each chunk as it lands, don't wait for 4096 bytes
                self.wfile.write(chunk)
                self.wfile.flush()
                size += len(chunk)
                HUB.publish(ex, "response_chunk", text=chunk.decode("utf-8", "replace"))
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
        except OSError as e:
            HUB.publish(ex, "error", message=f"stream broken: {e}")
        HUB.publish(ex, "response_end", duration=time.time() - t0, size=size)
        self.close_connection = True
        print(f"\n[{stamp()}] --- done", flush=True)


def main() -> None:
    global UPSTREAM, HISTORY
    parser = argparse.ArgumentParser(description="Print and forward an agent's API traffic.")
    parser.add_argument("--port", type=int, default=8041)
    parser.add_argument("--upstream", default=UPSTREAM, help="where to forward (default: the Anthropic API)")
    parser.add_argument("--history", type=int, default=1000, help="exchanges kept in memory for the dashboard")
    args = parser.parse_args()
    HISTORY = args.history
    UPSTREAM = args.upstream.rstrip("/")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Tap)
    print(f"proxy listening on http://localhost:{args.port}  ->  {UPSTREAM}")
    print(f"dashboard:  http://localhost:{args.port}/__dashboard")
    print(f"run the agent with:  ANTHROPIC_BASE_URL=http://localhost:{args.port} claude")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
