from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIGMA_APP = ROOT / "Dashboard and Real-Time Playback"
FIGMA_ENV_LOCAL = FIGMA_APP / ".env.local"

# ── Launch profiles ──────────────────────────────────────────────────
# Each profile is a dict with:
#   label      – shown in the menu
#   main_args  – extra flags forwarded to src/main.py
#   api        – start the FastAPI server?
#   api_host   – API host
#   api_port   – API port
#   livedata   – run livedata_export.py once?
#   frontend   – start the Vite frontend?

PROFILES: list[dict] = [
    {
        "label": "Production (DB + API + Frontend)  [recommended]",
        "main_args": ["--data-source", "db", "--export-figma-json"],
        "auto_run_simulation": False,
        "api": True,
        "api_host": "0.0.0.0",
        "api_port": 8001,
        "livedata": False,
        "frontend": True,
    },
    {
        "label": "Production (DB + file-based Live View + Frontend)",
        "main_args": ["--data-source", "db", "--export-figma-json"],
        "auto_run_simulation": False,
        "api": False,
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "livedata": True,
        "frontend": True,
    },
    {
        "label": "Simulation only — DB (no frontend)",
        "main_args": ["--data-source", "db", "--export-figma-json", "--no-frontend"],
        "auto_run_simulation": True,
        "api": False,
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "livedata": False,
        "frontend": False,
    },
    {
        "label": "Test data — CSV (no DB required)",
        "main_args": ["--data-source", "csv", "--export-figma-json"],
        "auto_run_simulation": False,
        "api": False,
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "livedata": False,
        "frontend": True,
    },
    {
        "label": "Test data — CSV, no frontend",
        "main_args": ["--data-source", "csv", "--no-frontend"],
        "auto_run_simulation": True,
        "api": False,
        "api_host": "127.0.0.1",
        "api_port": 8000,
        "livedata": False,
        "frontend": False,
    },
]


def _select_profile(timeout_sec: float = 15.0) -> dict:
    """Show an interactive menu and return the chosen profile.

    If stdin is not a TTY (e.g. piped / CI) or the user just presses Enter,
    profile #1 (production) is used.
    """
    if not sys.stdin.isatty():
        return PROFILES[0]

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║              Production Simulation Launcher             ║")
    print("╠══════════════════════════════════════════════════════════╣")
    for i, p in enumerate(PROFILES, 1):
        marker = " ◀ default" if i == 1 else ""
        print(f"║  [{i}] {p['label']:<47s}{marker:>1s} ║" if i == 1
              else f"║  [{i}] {p['label']:<57s} ║")
    print("╚══════════════════════════════════════════════════════════╝")

    try:
        choice = input(f"\nSelect mode [1-{len(PROFILES)}] (default 1): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return PROFILES[0]

    if choice == "":
        return PROFILES[0]

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(PROFILES):
            return PROFILES[idx]
    except ValueError:
        pass

    print(f"Invalid choice '{choice}', using default (1).")
    return PROFILES[0]


def run_main(python_exe: str, args: list[str]) -> None:
    try:
        subprocess.run([python_exe, str(ROOT / "src" / "main.py"), *args], check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Backend simulation failed (exit code {exc.returncode}). "
            "See the error message above."
        ) from None


def run_livedata(python_exe: str, extra_args: list[str]) -> None:
    """Run livedata_export.py once to refresh livedata.json before the frontend starts."""
    try:
        subprocess.run(
            [python_exe, str(ROOT / "src" / "livedata_export.py"), *extra_args],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"Warning: livedata_export failed (exit code {exc.returncode}). "
            "Live View will use the existing livedata.json if present.",
            file=sys.stderr,
        )


def _kill_listeners_on_port(port: int) -> None:
    """Kill any process currently listening on *port* (Windows only)."""
    import os as _os
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, errors="replace",
        )
    except Exception:
        return
    target = f":{port}"
    my_pid = _os.getpid()
    killed: set[int] = set()
    for line in out.splitlines():
        if target not in line or "LISTENING" not in line:
            continue
        parts = line.split()
        try:
            pid = int(parts[-1])
        except (ValueError, IndexError):
            continue
        if pid in (0, my_pid) or pid in killed:
            continue
        try:
            subprocess.call(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            killed.add(pid)
            print(f"[launch] Killed stale process PID {pid} on port {port}")
        except Exception:
            pass


def run_api(python_exe: str, host: str, port: int) -> subprocess.Popen:
    """Start the FastAPI Live View API server as a background process."""
    _kill_listeners_on_port(port)
    cmd = [
        python_exe, "-m", "uvicorn",
        "src.api:app",
        "--host", host,
        "--port", str(port),
    ]
    print(f"[launch] Starting Live View API on http://{host}:{port} ...")
    return subprocess.Popen(cmd, cwd=str(ROOT))


def run_frontend(open_browser: bool, npm_cmd: str) -> None:
    if not (FIGMA_APP / "package.json").exists():
        raise FileNotFoundError("Figma app folder not found")
    subprocess.run([npm_cmd, "install"], cwd=str(FIGMA_APP), check=True, shell=False)
    dev_process = subprocess.Popen([npm_cmd, "run", "dev"], cwd=str(FIGMA_APP), shell=False)
    if open_browser:
        time.sleep(2.0)
        webbrowser.open("http://localhost:5173")
    dev_process.wait()


def _configure_frontend_env(
    *,
    api_url: str | None = None,
    simulation_api_base: str | None = None,
    poll_ms: int = 30_000,
) -> None:
    """Write (or clear) the .env.local that tells the Vite frontend where to fetch live data.

    * api_url set   → VITE_LIVEDATA_JSON_URL + short poll interval
    * api_url None  → remove .env.local so the frontend falls back to public/livedata.json
    """
    if api_url:
        content = (
            f"# Auto-generated by launch.py — do not commit\n"
            f"VITE_LIVEDATA_JSON_URL={api_url}\n"
            f"VITE_SIMULATION_API_BASE_URL={simulation_api_base or ''}\n"
            f"VITE_LIVE_POLL_INTERVAL_MS={poll_ms}\n"
        )
        FIGMA_ENV_LOCAL.write_text(content, encoding="utf-8")
        print(f"[launch] Frontend configured → {api_url} (poll every {poll_ms // 1000}s)")
    else:
        if FIGMA_ENV_LOCAL.exists():
            FIGMA_ENV_LOCAL.unlink()
            print("[launch] Frontend configured → static livedata.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run simulation and start Figma dashboard app.",
        epilog="If no flags are given, an interactive menu is shown.",
    )
    parser.add_argument("--no-frontend", action="store_true", help="Only run simulation JSON export")
    parser.add_argument("--no-open-browser", action="store_true", help="Do not open the browser")
    parser.add_argument("--no-interactive", action="store_true", help="Skip the interactive menu and use CLI flags only")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use")
    parser.add_argument("--npm", default="npm", help="npm command (or full path) to use")
    parser.add_argument("--realtime-step-sec", type=float, default=60.0, help="Seconds per realtime step")
    parser.add_argument(
        "--run-simulation-now",
        action="store_true",
        help="Run the full simulation immediately on startup (legacy behavior).",
    )
    parser.add_argument(
        "--livedata",
        action="store_true",
        help="Refresh livedata.json from DB before starting the frontend (runs livedata_export.py once). Ignored when --api is set.",
    )
    parser.add_argument(
        "--livedata-input-filter",
        default=None,
        help="Forwarded to livedata_export.py as --input-filter when --livedata is set.",
    )
    # --- API mode ---
    parser.add_argument(
        "--api",
        action="store_true",
        help=(
            "Start the FastAPI Live View API server (src/api.py) instead of using "
            "the file-based livedata pipeline (livedata_export.py + scheduler.py). "
            "Requires fastapi and uvicorn to be installed."
        ),
    )
    parser.add_argument(
        "--api-host",
        default="127.0.0.1",
        help="Host for the API server (default: 127.0.0.1). Use 0.0.0.0 to expose on the network.",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="Port for the API server (default: 8000).",
    )
    args, unknown = parser.parse_known_args()

    # ── Decide whether to use interactive menu ──────────────────────
    # If the user passed explicit mode flags, skip the menu.
    _has_explicit_flags = (
        args.no_interactive
        or args.api
        or args.livedata
        or any(f.startswith("--data-source") for f in unknown)
    )

    if _has_explicit_flags:
        # Classic CLI-only path (no menu)
        should_run_simulation = args.run_simulation_now or args.no_frontend
        if should_run_simulation:
            main_args = ["--realtime-step-sec", str(args.realtime_step_sec)] + unknown
            run_main(args.python, main_args)

        api_process: subprocess.Popen | None = None
        if args.api:
            # Point the frontend at the live API
            _api_host_for_url = "127.0.0.1" if args.api_host == "0.0.0.0" else args.api_host
            _configure_frontend_env(
                api_url=f"http://{_api_host_for_url}:{args.api_port}/api/livedata",
                simulation_api_base=f"http://{_api_host_for_url}:{args.api_port}",
            )
            api_process = run_api(args.python, host=args.api_host, port=args.api_port)
            time.sleep(1.5)
        elif args.livedata:
            _configure_frontend_env(api_url=None)  # static file mode
            livedata_args: list[str] = []
            if args.livedata_input_filter:
                livedata_args += ["--input-filter", args.livedata_input_filter]
            run_livedata(args.python, livedata_args)
        else:
            _configure_frontend_env(api_url=None)

        do_frontend = not args.no_frontend
    else:
        # Interactive mode
        profile = _select_profile()
        print(f"\n→  {profile['label']}\n")

        should_run_simulation = profile.get("auto_run_simulation", False) or args.run_simulation_now
        if should_run_simulation:
            main_args = ["--realtime-step-sec", str(args.realtime_step_sec)] + profile["main_args"] + unknown
            run_main(args.python, main_args)

        api_process = None
        if profile["api"]:
            # Point the frontend at the live API
            _api_host_for_url = "127.0.0.1" if profile["api_host"] == "0.0.0.0" else profile["api_host"]
            _configure_frontend_env(
                api_url=f"http://{_api_host_for_url}:{profile['api_port']}/api/livedata",
                simulation_api_base=f"http://{_api_host_for_url}:{profile['api_port']}",
            )
            api_process = run_api(args.python, host=profile["api_host"], port=profile["api_port"])
            time.sleep(1.5)
        elif profile["livedata"]:
            _configure_frontend_env(api_url=None)  # static file mode
            livedata_args = []
            if args.livedata_input_filter:
                livedata_args += ["--input-filter", args.livedata_input_filter]
            run_livedata(args.python, livedata_args)
        else:
            _configure_frontend_env(api_url=None)

        do_frontend = profile["frontend"] and not args.no_frontend

    if do_frontend:
        npm_cmd = args.npm
        resolved = shutil.which(npm_cmd)
        if resolved is None:
            resolved = shutil.which("npm.cmd")
        if resolved is None:
            raise FileNotFoundError(
                "npm not found. Install Node.js or provide --npm with the full path to npm.cmd."
            )
        npm_cmd = resolved
        try:
            run_frontend(open_browser=not args.no_open_browser, npm_cmd=npm_cmd)
        finally:
            if api_process is not None:
                api_process.terminate()
    else:
        # If no frontend but API is running, wait for Ctrl+C
        if api_process is not None:
            print("[launch] API server running. Press Ctrl+C to stop.")
            try:
                api_process.wait()
            except KeyboardInterrupt:
                api_process.terminate()


if __name__ == "__main__":
    main()
