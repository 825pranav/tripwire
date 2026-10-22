"""`make start`: bring the whole demo up from a cold machine with one command.

Starts Docker Desktop and Ollama if they are not running, pulls the model if it is
missing, starts the compose stack together with the realtime simulator, waits until the
doctor checks pass, then opens the dashboard. Safe to re-run on a stack that is already up.

    python -m tripwire.ops.start [--no-browser] [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

from tripwire.common.config import Settings, load_settings
from tripwire.ops.doctor import print_table, run_checks

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_URL = "http://localhost:8000"
DOCKER_DESKTOP_WIN = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
OLLAMA_CHECK = "ollama: reachable, model pulled"


def step(message: str) -> None:
    print(f"==> {message}", flush=True)


def wait_for(probe: Callable[[], bool], timeout_s: float, interval_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(interval_s)
    return False


def docker_ready() -> bool:
    result = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return result.returncode == 0


def ensure_docker() -> None:
    if shutil.which("docker") is None:
        sys.exit("docker not found on PATH: install Docker Desktop (or Docker Engine) first")
    if docker_ready():
        step("docker: running")
        return
    if sys.platform == "win32" and DOCKER_DESKTOP_WIN.exists():
        subprocess.Popen([str(DOCKER_DESKTOP_WIN)])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-a", "Docker"], check=False)
    else:
        sys.exit("docker daemon is not running: start it (e.g. `sudo systemctl start docker`)")
    step("docker: starting Docker Desktop (can take a minute)")
    if not wait_for(docker_ready, timeout_s=180):
        sys.exit("docker did not become ready within 3 minutes")
    step("docker: running")


def ollama_models(host: str) -> set[str] | None:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            return {m["name"] for m in json.load(resp)["models"]}
    except OSError:
        return None


def ensure_ollama(settings: Settings) -> bool:
    """Start Ollama and pull the model if needed. False means reports will wait for it."""
    models = ollama_models(settings.ollama_host)
    if models is None:
        if shutil.which("ollama") is None:
            step("ollama: not installed; alerts still flow, case reports wait for a model")
            return False
        step("ollama: starting `ollama serve`")
        detached = sys.platform == "win32"
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08 | 0x200 if detached else 0,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            start_new_session=not detached,
        )
        if not wait_for(lambda: ollama_models(settings.ollama_host) is not None, timeout_s=30):
            step("ollama: did not come up; alerts still flow, case reports wait for it")
            return False
        models = ollama_models(settings.ollama_host) or set()
    wanted = settings.ollama_model
    if wanted not in models and f"{wanted}:latest" not in models:
        step(f"ollama: pulling {wanted} (first run only)")
        if subprocess.run(["ollama", "pull", wanted], check=False).returncode != 0:
            step(f"ollama: pull failed; case reports wait until `ollama pull {wanted}` works")
            return False
    step(f"ollama: {wanted} ready")
    return True


def start_stack() -> None:
    step("compose: building and starting kafka, postgres, services and the simulator")
    cmd = ["docker", "compose", "--profile", "sim", "up", "-d", "--build", "--wait"]
    if subprocess.run(cmd, cwd=ROOT, check=False).returncode != 0:
        sys.exit("docker compose up failed; see the output above (`make logs` for more)")


def wait_healthy(settings: Settings, timeout_s: float, ollama_ok: bool) -> bool:
    """Poll the doctor checks until nothing fails (services need a few heartbeats)."""
    step("doctor: waiting for every service to report healthy")
    deadline = time.monotonic() + timeout_s
    while True:
        results = run_checks(settings)
        failing = [r for r in results if r[1] == "FAIL" and (ollama_ok or r[0] != OLLAMA_CHECK)]
        if not failing or time.monotonic() >= deadline:
            print()
            print_table(results)
            return not failing
        time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard")
    parser.add_argument("--timeout", type=float, default=120, help="seconds to wait for health")
    args = parser.parse_args()
    os.chdir(ROOT)
    settings = load_settings()

    ensure_docker()
    ollama_ok = ensure_ollama(settings)
    start_stack()
    healthy = wait_healthy(settings, args.timeout, ollama_ok)

    print()
    if healthy:
        step(f"Tripwire is up: {DASHBOARD_URL}")
    else:
        step(f"started, but some checks still fail (see above); dashboard: {DASHBOARD_URL}")
    print("    make doctor   health table     make logs   follow logs     make down   stop")
    if not args.no_browser:
        webbrowser.open(DASHBOARD_URL)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
