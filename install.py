"""One-step installer for sfm-mcp (Windows).

    py install.py                     install / update everything
    py install.py --sfm-dir "D:\\SteamLibrary\\steamapps\\common\\SourceFilmmaker\\game"
    py install.py --no-claude         skip registering with Claude Code
    py install.py --uninstall         remove the SFM hook and the Claude Code entry

Steps:
  1. creates .venv next to this file and installs requirements.txt
  2. installs the bridge hook into SFM (install_bridge.py)
  3. registers the MCP server "sfm" with Claude Code (user scope), if the
     `claude` command is available; otherwise prints the JSON config for
     Claude Desktop / other MCP clients.
Needs Python 3.11+ (only for the MCP server; SFM keeps its own Python 2.7).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / "Scripts" / "python.exe"
SERVER = ROOT / "server" / "sfm_mcp_server.py"
NAME = "sfm"


def run(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    print("  >", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    return subprocess.run(cmd, check=check, **kw)


def arg_value(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def make_venv() -> None:
    print("[1/3] Python environment")
    if not VPY.is_file():
        uv = shutil.which("uv")
        if uv:
            run([uv, "venv", str(VENV), "--python", sys.executable])
        else:
            run([sys.executable, "-m", "venv", str(VENV)])
    uv = shutil.which("uv")
    req = str(ROOT / "requirements.txt")
    if uv:
        run([uv, "pip", "install", "--python", str(VPY), "-r", req])
    else:
        run([str(VPY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", req])


def install_bridge(extra: list[str]) -> bool:
    print("[2/3] SFM bridge hook")
    r = run([str(VPY), str(ROOT / "install_bridge.py"), *extra], check=False)
    return r.returncode == 0


def claude_cmd() -> str | None:
    c = shutil.which("claude")
    if c:
        return c
    local = Path.home() / ".local" / "bin" / "claude.exe"
    return str(local) if local.is_file() else None


def register(sfm_dir: str | None) -> None:
    print("[3/3] MCP client registration")
    env = {"SFM_GAME_DIR": sfm_dir} if sfm_dir else {}
    claude = None if "--no-claude" in sys.argv else claude_cmd()
    if claude:
        run([claude, "mcp", "remove", NAME, "--scope", "user"], check=False,
            capture_output=True)
        cmd = [claude, "mcp", "add", NAME, "--scope", "user"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["--", str(VPY), str(SERVER)]
        r = run(cmd, check=False)
        if r.returncode == 0:
            print(f'  registered with Claude Code as "{NAME}" (user scope)')
            return
        print("  could not register with Claude Code automatically")
    conf = {"mcpServers": {NAME: {"command": str(VPY), "args": [str(SERVER)]}}}
    if env:
        conf["mcpServers"][NAME]["env"] = env
    print("  Add this to your MCP client config (Claude Desktop:")
    print("  %APPDATA%\\Claude\\claude_desktop_config.json):")
    print(json.dumps(conf, indent=2))


def uninstall(extra: list[str]) -> int:
    ok = False
    if VPY.is_file():
        ok = run([str(VPY), str(ROOT / "install_bridge.py"), "--uninstall", *extra],
                 check=False).returncode == 0
    else:
        print("  .venv is missing, so the SFM hook could not be checked.")
    claude = claude_cmd()
    if claude:
        run([claude, "mcp", "remove", NAME, "--scope", "user"], check=False)
    if not ok:
        print("\nThe SFM hook was NOT removed. Run again with --sfm-dir \"...\\SourceFilmmaker\\game\",")
        print("or delete the '# >>> sfm-mcp bridge >>>' block from usermod\\scripts\\sfm\\sfm_init.py.")
        return 1
    print("Uninstalled. You can delete this folder now.")
    return 0


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # keep output in order with child processes
    if os.name != "nt":
        print("Source Filmmaker runs on Windows only.")
        return 1
    if sys.version_info < (3, 11):
        print(f"Python 3.11+ is needed (this is {sys.version.split()[0]}). "
              "Get it from https://www.python.org/downloads/ and run: py -3 install.py")
        return 1
    sfm_dir = arg_value("--sfm-dir")
    extra = ["--sfm-dir", sfm_dir] if sfm_dir else []
    if "--uninstall" in sys.argv:
        return uninstall(extra)
    make_venv()
    if not install_bridge(extra):
        print("\nSFM was not found. Run again with --sfm-dir \"...\\SourceFilmmaker\\game\"")
        return 1
    register(sfm_dir)
    print("\nDone. Restart Source Filmmaker (the bridge starts with it) and your MCP")
    print("client (e.g. Claude Code), then ask Claude to call sfm_status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
