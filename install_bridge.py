"""Install / uninstall the sfm-mcp bridge hook into SFM's usermod sfm_init.py.

    python install_bridge.py            install (or update) the hook
    python install_bridge.py --uninstall
    python install_bridge.py --status

The hook is a marked block appended to
<SFM>/game/usermod/scripts/sfm/sfm_init.py. The original file is backed up
once to sfm_init.py.sfm-mcp-backup. Two small helper scripts are copied too:
  usermod/scripts/sfm/mainmenu/sfm-mcp/sfm_mcp_context.py   (Scripts menu)
  usermod/scripts/sfm/animset/sfm_mcp_context.py            (right-click > Rig)
Nothing else in SFM is modified.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))
from sfm_mcp_server import HOOK_MARK, SFM_SIDE, sfm_game_dir  # noqa: E402

END_MARK = "# <<< sfm-mcp bridge <<<"


def hook_block(eol: str = "\n") -> str:
    # Pure ASCII: SFM's Python 2.7 refuses a whole source file that has a
    # non-ASCII byte and no coding line (PEP 263), so the path is written as
    # a u"" literal with \u escapes and encoded for the file system at runtime.
    side = str(SFM_SIDE).encode("unicode_escape").decode("ascii")
    return f"""{HOOK_MARK}
# Starts the sfm-mcp bridge (127.0.0.1, token-protected). Remove this block
# or run install_bridge.py --uninstall to disable it.
try:
    import sys as _sfmmcp_sys
    _sfmmcp_dir = u"{side}"
    try:
        _sfmmcp_dir = _sfmmcp_dir.encode(_sfmmcp_sys.getfilesystemencoding() or "mbcs")
    except UnicodeError:
        pass
    if _sfmmcp_dir not in _sfmmcp_sys.path:
        _sfmmcp_sys.path.insert(0, _sfmmcp_dir)
    import sfm_mcp_bridge
    sfm_mcp_bridge.start()
except Exception:
    import traceback as _sfmmcp_tb
    _sfmmcp_tb.print_exc()
{END_MARK}
""".replace("\n", eol)


def add_hook(text: str) -> str:
    """Append the hook, in the file's own line-ending style; strip_hook()
    gives `text` back byte for byte (if it is empty or ends with a newline)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    sep = eol if text and not text.endswith("\n") else ""
    return text + sep + hook_block(eol)


def strip_hook(text: str) -> str:
    """Remove exactly the marked block (start marker to end-marker line)."""
    i = text.find(HOOK_MARK)
    if i < 0:
        return text
    j = text.find(END_MARK, i)
    if j < 0:
        # Without the end marker everything after the start marker would go.
        raise ValueError("sfm-mcp end marker is missing; remove the block by hand")
    j += len(END_MARK)
    for nl in ("\r\n", "\n"):
        if text.startswith(nl, j):
            j += len(nl)
            break
    return text[:i] + text[j:]


def main() -> int:
    for i, a in enumerate(sys.argv):
        if a == "--sfm-dir" and i + 1 < len(sys.argv):
            import os
            os.environ["SFM_GAME_DIR"] = sys.argv[i + 1]
    g = sfm_game_dir()
    if not g:
        print("SFM not found. Set SFM_GAME_DIR to ...\\SourceFilmmaker\\game")
        return 1
    init = g / "usermod" / "scripts" / "sfm" / "sfm_init.py"
    backup = init.with_name(init.name + ".sfm-mcp-backup")
    menu_dir = init.parent / "mainmenu" / "sfm-mcp"
    menu_src = SFM_SIDE / "menu"
    animset_dir = init.parent / "animset"
    animset_src = SFM_SIDE / "menu_animset"
    # Bytes in, bytes out (no newline translation); latin-1 maps every byte to
    # one char and back, so the user's own code keeps its exact bytes.
    text = init.read_bytes().decode("latin-1") if init.is_file() else ""

    if "--status" in sys.argv:
        print(f"{init}: hook {'INSTALLED' if HOOK_MARK in text else 'not installed'}")
        print(f"backup: {backup if backup.exists() else 'none'}")
        return 0

    try:
        stripped = strip_hook(text)
    except ValueError as e:
        print(f"{init}: {e}")
        return 1

    if "--uninstall" in sys.argv:
        if menu_dir.is_dir():
            shutil.rmtree(menu_dir)
            print(f"menu scripts removed: {menu_dir}")
        for f in animset_src.glob("*.py"):
            if (animset_dir / f.name).is_file():
                (animset_dir / f.name).unlink()
                print(f"animset script removed: {animset_dir / f.name}")
        if HOOK_MARK not in text:
            print("hook not installed")
            return 0
        init.write_bytes(stripped.encode("latin-1"))
        print(f"hook removed from {init}")
        return 0

    menu_dir.mkdir(parents=True, exist_ok=True)
    for f in menu_src.glob("*.py"):
        shutil.copy2(f, menu_dir / f.name)
        print(f"menu script -> {menu_dir / f.name}")
    animset_dir.mkdir(parents=True, exist_ok=True)
    for f in animset_src.glob("*.py"):
        shutil.copy2(f, animset_dir / f.name)
        print(f"animset script -> {animset_dir / f.name}")

    init.parent.mkdir(parents=True, exist_ok=True)
    if init.is_file() and not backup.exists():
        shutil.copy2(init, backup)
        print(f"backup -> {backup}")
    init.write_bytes(add_hook(stripped).encode("latin-1"))
    print(f"hook installed -> {init}")
    try:  # SFM's Python 2.7 imports only from paths in the ANSI code page
        str(SFM_SIDE).encode("mbcs")
    except (UnicodeEncodeError, LookupError):
        print(f"WARNING: SFM cannot load the bridge from {SFM_SIDE}; move this folder "
              "to a plain path such as C:\\Tools\\sfm-mcp and run the installer again.")
    print("Restart SFM to activate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
