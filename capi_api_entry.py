"""
CAPI API Server — entry point for the compiled executable.

Usage:
    capi_api.exe                        → starts on default port 48001
    capi_api.exe --port 8001            → starts on custom port
    capi_api.exe --host 0.0.0.0 --port 48001
"""
# freeze_support() MUST be the very first call when running as a PyInstaller
# bundle on Windows.  When uvicorn (or any multiprocessing code) spawns a
# child process it re-executes this exe with --multiprocessing-fork arguments.
# freeze_support() intercepts those arguments before argparse sees them,
# handles the child-process bootstrap, and exits — preventing the
# "unrecognized arguments: --multiprocessing-fork …" error.
import multiprocessing
multiprocessing.freeze_support()

import argparse
import logging
import sys
import os

# Suppress the noisy-but-harmless Windows asyncio "ConnectionResetError: [WinError 10054]"
# that appears when a browser closes the TCP connection before uvicorn finishes cleanup.
# The server keeps running fine — this is purely cosmetic log noise on Windows ProactorEventLoop.
class _SuppressWinError10054(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "WinError 10054" not in msg and "ConnectionResetError" not in msg

for _logger_name in ("asyncio", "uvicorn.error", "uvicorn"):
    logging.getLogger(_logger_name).addFilter(_SuppressWinError10054())

# When running as a PyInstaller bundle, add the extracted src/ to sys.path
if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS  # type: ignore[attr-defined]
    src_dir = os.path.join(bundle_dir, 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

import uvicorn

def main():
    parser = argparse.ArgumentParser(description="CAPI API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=48001, help="Bind port (default: 48001)")
    parser.add_argument("--ssl-certfile", default=None, help="Path to SSL certificate file (.pem)")
    parser.add_argument("--ssl-keyfile", default=None, help="Path to SSL private key file (.pem)")
    parser.add_argument(
        "--workers", type=int, default=1,
        help=(
            "Number of uvicorn worker processes (default: 1). "
            "Use 2–4 on multi-core servers to handle concurrent users. "
            "NOTE: each worker is an independent process with its own in-memory "
            "cache and simulation job state — keep at 1 if only one user runs "
            "simulations at a time to avoid job-state inconsistency."
        ),
    )
    args = parser.parse_args()

    scheme = "https" if args.ssl_certfile else "http"

    # Multi-worker mode is not supported inside a PyInstaller frozen exe on
    # Windows: the spawned child processes re-execute the exe entry point and
    # fail because they receive --multiprocessing-fork arguments that argparse
    # does not recognise.  Clamp to 1 worker silently when running frozen.
    workers = args.workers
    if getattr(sys, "frozen", False) and workers > 1:
        print(
            f"[CAPI] WARNING: --workers {workers} is not supported in the frozen exe on Windows. "
            "Falling back to --workers 1. "
            "Performance improvements (GZip + snapshot cache) are still active."
        )
        workers = 1

    print(f"[CAPI] Starting API server on {scheme}://{args.host}:{args.port} (workers={workers})")

    uvicorn_kwargs: dict = dict(
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )

    if workers > 1:
        # Multi-process mode: uvicorn spawns workers via the string import path.
        # This requires the module to be importable (works fine inside the bundle
        # because sys.path was already patched above).
        uvicorn_kwargs["workers"] = workers
        uvicorn.run("api:app", **uvicorn_kwargs)
    else:
        # Single-process mode (default): pass the app object directly — avoids
        # an extra import round-trip and keeps behaviour identical to before.
        from api import app
        uvicorn.run(app, **uvicorn_kwargs)

if __name__ == "__main__":
    main()
