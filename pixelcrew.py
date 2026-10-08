#!/usr/bin/env python3
"""PixelCrew: Claude Code, Codex, Gemini and Grok working as one team, in a pixel-art office.

    python3 pixelcrew.py            # http://127.0.0.1:8787
    python3 pixelcrew.py --port 9000 --no-browser
"""
import argparse
import os
import sys
import threading
import time
import webbrowser

if sys.version_info < (3, 8):
    sys.exit("PixelCrew needs Python 3.8 or newer.")

import signal  # noqa: E402

from server.app import serve, shutdown  # noqa: E402
from server.bus import mark_interrupted  # noqa: E402
from server.status import CLAUDE_USAGE  # noqa: E402
from server.config import DATA_DIR, DEFAULT_WORKSPACE, HEALTH, load_settings  # noqa: E402


def main() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description="A pixel-art office where AI coding agents work as one team")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1", help="Only loopback addresses are recommended.")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if load_settings()["workspace"] == str(DEFAULT_WORKSPACE):
        DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    interrupted = mark_interrupted()
    CLAUDE_USAGE.request(force=True)
    try:
        server = serve(args.host, args.port)
    except OSError as exc:
        sys.exit(f"Could not open port {args.port} ({exc}). Try another one: python3 pixelcrew.py --port 8790")

    url = f"http://127.0.0.1:{args.port}"
    print(f"\n  PixelCrew is running: {url}")
    print(f"  Working folder:       {load_settings()['workspace']}")
    health = HEALTH.get(refresh=True)
    for name, info in health.items():
        mark = "ready" if info.get("ready") else f"NOT READY: {info.get('problem')}  ->  {info.get('fix') or ''}"
        print(f"  {name:<7} {info.get('version') or '-':<28} {mark}")
    if interrupted:
        print(f"  Marked {interrupted} run(s) left over from the last session as interrupted.")
    print("  Press Ctrl+C to stop\n")
    # Closing the terminal sends SIGTERM/SIGHUP; treat them like Ctrl+C.
    def stop(*_: object) -> None:
        raise KeyboardInterrupt()

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)

    # When launched by PixelCrew.app, stop if the app goes away (crash, force quit)
    # so no orphaned server keeps agents running.
    parent = os.environ.get("PIXELCREW_PARENT_PID")
    if parent and parent.isdigit():
        def watch_parent() -> None:
            while True:
                time.sleep(2)
                if os.getppid() != int(parent):
                    os.kill(os.getpid(), signal.SIGTERM)
                    return
        threading.Thread(target=watch_parent, daemon=True).start()

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down and stopping running agents…")
    finally:
        shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
