#!/usr/bin/env python3
"""Build and manage a loopback-only SQLite demo without Docker."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
PYTHON = ROOT / ".venv/bin/python"
STATE = LOCAL / "server.json"


def running():
    if not STATE.exists():
        return None
    state = json.loads(STATE.read_text())
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(state["pid"]), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    # A stale PID must never cause us to stop another process.
    if str(ROOT / "apps/api") in command and "uvicorn app.main:app" in command:
        return state
    return None


def stop():
    state = running()
    if state:
        os.kill(state["pid"], signal.SIGTERM)
        for _ in range(50):
            if not running():
                break
            time.sleep(0.1)
        else:
            raise SystemExit("Server is still stopping; inspect .local/server.log.")
    STATE.unlink(missing_ok=True)
    print("Local server stopped.")


def start(port):
    state = running()
    if state:
        print(f"Already running: http://127.0.0.1:{state['port']}/app-next/ (PID {state['pid']})")
        return
    if not PYTHON.exists():
        raise SystemExit("Create .venv and install .[dev] first; see docs/LOCAL_DEVELOPMENT.md.")
    if not (ROOT / "apps/web-react/dist/index.html").exists():
        raise SystemExit("Run the build command first.")
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"Port {port} is unavailable: {exc}") from exc
    database = f"sqlite+pysqlite:///{LOCAL / 'app.db'}"
    env = os.environ | {
        "PYTHONPATH": str(ROOT / "apps/api"),
        "DATABASE_URL": database,
        "PLATFORM_DATABASE_URL": database,
        "MIGRATION_DATABASE_URL": database,
        "OBJECT_STORAGE_PATH": str(LOCAL / "storage"),
        "DEMO_MODE": "true",
        "AUTH_MODE": "demo",
        "APP_ENV": "development",
        "API_PREFIX": "/api/v1",
        "REQUIRE_HTTPS": "false",
        "ALLOWED_HOSTS": '["localhost", "127.0.0.1"]',
        "CORS_ORIGINS": json.dumps(
            [f"http://127.0.0.1:{port}", f"http://localhost:{port}", "http://localhost:5173"]
        ),
    }
    with (LOCAL / "setup.log").open("a") as log:
        for args in (["-m", "alembic", "upgrade", "head"], ["-m", "app.seed"]):
            subprocess.run(
                [str(PYTHON), *args],
                cwd=ROOT / "apps/api",
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    with (LOCAL / "server.log").open("a") as log:
        process = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(ROOT / "apps/api"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    STATE.write_text(json.dumps({"pid": process.pid, "port": port}) + "\n")
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/v1/ready", timeout=0.5) as response:
                if response.status == 200:
                    print(f"Running: http://127.0.0.1:{port}/app-next/ (PID {process.pid})")
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    STATE.unlink(missing_ok=True)
    raise SystemExit("Startup failed; inspect .local/setup.log and .local/server.log.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "start", "stop", "status"])
    parser.add_argument("--port", type=int, default=18000, metavar="PORT")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    LOCAL.mkdir(exist_ok=True)
    with (LOCAL / "command.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "build":
            subprocess.run(["npm", "run", "build"], cwd=ROOT / "apps/web-react", check=True)
        elif args.command == "start":
            start(args.port)
        elif args.command == "stop":
            stop()
        else:
            state = running()
            print(
                f"Running: http://127.0.0.1:{state['port']}/app-next/ (PID {state['pid']})"
                if state
                else "Local server is not running."
            )


if __name__ == "__main__":
    main()
