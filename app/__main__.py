"""Run the app with one command:

    python -m app

Starts the server and opens the web UI in the default browser.
"""

import argparse
import socket
import sys
import threading
import webbrowser

import uvicorn


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app", description="Run geomeasure-api locally.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the web UI")
    parser.add_argument("--reload", action="store_true", help="restart when source files change")
    args = parser.parse_args()

    # Uvicorn's own error for a busy port is a long traceback; say what to do instead.
    if port_in_use(args.host, args.port):
        sys.exit(f"Port {args.port} is already in use. Try: python -m app --port {args.port + 1}")

    browse_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{browse_host}:{args.port}"
    print(f"geomeasure-api running at {url}  (API docs: {url}/docs, stop with Ctrl+C)")
    if not args.no_browser:
        # Give the server a moment to bind before the browser asks for the page.
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
