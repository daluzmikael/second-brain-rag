"""Run the server as a launchd user agent.

The point is latency: a cold start pays ~10s of Python import time, which dwarfs
the few milliseconds the search itself takes. A resident process pays it once.

IMPORTANT -- this does not work when the project or the vault lives under a
folder macOS protects with TCC (~/Documents, ~/Desktop, ~/Downloads, iCloud
Drive). A launchd agent has no access to those and every read fails with
"Operation not permitted", which surfaces as odd errors like EDEADLK during
imports. `install` checks for this and says so.

For a vault in a protected folder, use SecondBrain.app instead (see mac/): a GUI
app is granted access by the user and its child processes inherit that grant.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from app.config import HOST, PORT, PROJECT_ROOT

LABEL = "com.secondbrain.server"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = AGENTS_DIR / f"{LABEL}.plist"
LOG_PATH = PROJECT_ROOT / "data" / "server.log"


def _python() -> Path:
    """The venv interpreter, falling back to whatever is running us."""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    return venv_python if venv_python.exists() else Path(sys.executable)


def _domain() -> str:
    import os

    return f"gui/{os.getuid()}"


def _plist() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(_python()),
            "-m",
            "app.cli",
            "serve",
            "--no-browser",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "info",
        ],
        "WorkingDirectory": str(PROJECT_ROOT),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            # launchd gives an agent a minimal PATH.
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(Path.home()),
        },
        "RunAtLoad": True,
        # Restart if it dies, but not if it exited cleanly (a deliberate stop).
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


# Folders macOS gates behind TCC. A launchd agent is refused all of them.
_PROTECTED = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")


def _protected_paths() -> list[tuple[str, Path]]:
    from app.config import VAULT_PATH

    home = Path.home()
    found = []
    for label, path in (("project", PROJECT_ROOT), ("vault", VAULT_PATH)):
        try:
            relative = path.resolve().relative_to(home)
        except ValueError:
            continue
        if any(str(relative).startswith(folder) for folder in _PROTECTED):
            found.append((label, path))
    return found


def install() -> int:
    blocked = _protected_paths()
    if blocked:
        print("A launchd agent cannot read these -- macOS blocks background")
        print("agents from TCC-protected folders:\n")
        for label, path in blocked:
            print(f"  {label:8} {path}")
        print(
            "\nUse the menu bar app instead, which is granted access as a normal app:\n"
            "    ./mac/build.sh\n"
            "    open ~/Applications/SecondBrain.app\n"
            "\nIt starts the server itself and keeps it running while it is open.\n"
            "(To install the agent anyway, grant Full Disk Access to\n"
            f" {_python()} in System Settings > Privacy & Security.)",
            file=sys.stderr,
        )
        return 1

    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(_plist(), handle)
    print(f"wrote {PLIST_PATH}")

    # Replace any previous registration.
    _run(["launchctl", "bootout", _domain(), str(PLIST_PATH)])
    result = _run(["launchctl", "bootstrap", _domain(), str(PLIST_PATH)])
    if result.returncode != 0:
        # Older macOS, or already loaded.
        legacy = _run(["launchctl", "load", "-w", str(PLIST_PATH)])
        if legacy.returncode != 0:
            print(result.stderr.strip() or legacy.stderr.strip(), file=sys.stderr)
            return 1

    _run(["launchctl", "kickstart", "-k", f"{_domain()}/{LABEL}"])
    print(f"service running at http://{HOST}:{PORT}")
    print("it will start automatically at login")
    print(f"logs: {LOG_PATH}")
    return 0


def uninstall() -> int:
    _run(["launchctl", "bootout", _domain(), str(PLIST_PATH)])
    _run(["launchctl", "unload", "-w", str(PLIST_PATH)])
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
        print(f"removed {PLIST_PATH}")
    print("service stopped and will no longer start at login")
    return 0


def restart() -> int:
    if not PLIST_PATH.exists():
        print("service is not installed -- run: ./brain service install", file=sys.stderr)
        return 1
    _run(["launchctl", "kickstart", "-k", f"{_domain()}/{LABEL}"])
    print("service restarted")
    return 0


def status() -> int:
    installed = PLIST_PATH.exists()
    print(f"plist       {PLIST_PATH}  ({'present' if installed else 'not installed'})")

    result = _run(["launchctl", "print", f"{_domain()}/{LABEL}"])
    if result.returncode == 0:
        state = pid = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("state = "):
                state = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("pid = "):
                pid = stripped.split("=", 1)[1].strip()
        print(f"launchd     state={state} pid={pid}")
    else:
        print("launchd     not loaded")

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/status", timeout=3) as response:
            import json

            info = json.loads(response.read())
        print(f"http        responding on port {PORT}")
        print(f"index       {info.get('notes')} notes, {info.get('chunks')} chunks")
        print(f"embeddings  {info.get('embedding_identity_config')}")
        print(f"auto-index  every {info.get('auto_reindex_seconds')}s")
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        print(f"http        not responding ({error})")
    return 0


def logs(lines: int = 40) -> int:
    if not LOG_PATH.exists():
        print(f"no log yet at {LOG_PATH}")
        return 0
    result = _run(["tail", "-n", str(lines), str(LOG_PATH)])
    print(result.stdout, end="")
    return 0
