"""
Entry point for the standalone build.

Running server.py directly is fine from a terminal, where a traceback stays on
screen. A double-clicked .exe closes its console the instant the process dies,
so any error is invisible — the window "opens and shuts" and there is nothing
to go on. Everything here exists to prevent that:

  - the port is checked before uvicorn touches it, because "address already in
    use" is the single most common cause and it is not an error at all, it
    means the program is already running
  - any exception is written to error.log next to the .exe
  - the window is held open on failure so the message can be read
"""

import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime

HOST = "127.0.0.1"
PORT = 8000

# A frozen build writes to a pipe rather than a terminal in some launch
# contexts, and Python then buffers stdout — the banner and the first-run
# admin password would appear only after the program exits, which is far too
# late to be useful.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def app_dir() -> str:
    """The folder holding the .exe, which is where data must live."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def is_our_server(host: str, port: int) -> bool:
    """
    Distinguish our own instance from some unrelated program on the port.

    Probes /reception because that is the one route guaranteed to exist and
    to be what main() actually opens the browser to. This used to probe
    /login, a route that belonged to the authentication system removed from
    this app — the request 404'd, the check always returned False, and a
    second launch while already running silently started a second instance
    on the next free port instead of reopening the browser.
    """
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://{host}:{port}/reception", timeout=2) as r:
            return "MB Ballet" in r.read(4000).decode("utf-8", "ignore")
    except Exception:
        return False


def free_port(host: str, start: int, tries: int = 20) -> int:
    for p in range(start, start + tries):
        if not port_in_use(host, p):
            return p
    return 0


def log_error(exc_text: str) -> str:
    path = os.path.join(app_dir(), "error.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*70}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n{'='*70}\n")
        f.write(exc_text)
    return path


def hold(message: str = "") -> None:
    if message:
        print(message)
    print()
    try:
        input("  Press Enter to close this window...")
    except EOFError:
        time.sleep(20)


def banner(port: int) -> None:
    print()
    print("  " + "=" * 56)
    print("    MB BALLET ACADEMY")
    print("  " + "=" * 56)
    print()
    print(f"    The system is running at  http://{HOST}:{port}")
    print()
    print("    Your browser should open by itself. If it does not,")
    print("    type that address into the address bar.")
    print()
    print("    KEEP THIS WINDOW OPEN while using the system.")
    print("    Closing it stops the program.")
    print("  " + "-" * 56)
    print()


def main() -> int:
    os.chdir(app_dir())

    # Already running? Not an error — just show it to them again.
    if port_in_use(HOST, PORT):
        if is_our_server(HOST, PORT):
            print()
            print("  The academy system is already running.")
            print(f"  Opening it at http://{HOST}:{PORT}")
            webbrowser.open(f"http://{HOST}:{PORT}/reception")
            time.sleep(3)
            return 0

        port = free_port(HOST, PORT + 1)
        if not port:
            hold("  Could not find a free port to run on. Restart the computer "
                 "and try again.")
            return 1
        print(f"\n  Port {PORT} is taken by another program.")
        print(f"  Using port {port} instead.\n")
    else:
        port = PORT

    try:
        import uvicorn
        from server import app
    except Exception:
        text = traceback.format_exc()
        path = log_error(text)
        print("\n  The program could not start up.\n")
        print(text)
        hold(f"  This was also written to:\n  {path}")
        return 1

    def open_browser():
        time.sleep(2.0)
        webbrowser.open(f"http://{HOST}:{port}/reception")

    threading.Thread(target=open_browser, daemon=True).start()
    banner(port)

    uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()      # onefile builds re-exec themselves
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        code = 0
    except SystemExit as e:
        code = int(e.code or 0)
        if code:
            hold("  The program exited early.")
    except Exception:
        text = traceback.format_exc()
        path = log_error(text)
        print("\n  Something went wrong:\n")
        print(text)
        hold(f"  This was also written to:\n  {path}\n"
             f"  Send that file to whoever maintains the system.")
        code = 1
    sys.exit(code)
