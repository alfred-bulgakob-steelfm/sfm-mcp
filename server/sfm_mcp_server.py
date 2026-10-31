"""sfm-mcp: MCP server for Source Filmmaker.

Runs outside SFM (Python 3). Talks to the bridge inside SFM
(sfm_side/sfm_mcp_bridge.py, Python 2.7) over 127.0.0.1 with a shared token.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

VERSION = "0.4.0"
HOST = "127.0.0.1"
PORT = int(os.environ.get("SFM_MCP_PORT", "9877"))
CONF_DIR = Path.home() / ".sfm_mcp"
TOKEN_FILE = CONF_DIR / "token"
SHOTS_DIR = CONF_DIR / "shots"
ROOT = Path(__file__).resolve().parent.parent
SFM_SIDE = ROOT / "sfm_side"
HOOK_MARK = "# >>> sfm-mcp bridge >>>"

INSTRUCTIONS = """\
Control a running Source Filmmaker (SFM) instance.

Workflow: sfm_status -> (sfm_launch if needed) -> sfm_session_info ->
sfm_exec / helpers -> sfm_screenshot to check the result visually.

Facts about SFM scripting (sfm_exec):
- Code runs INSIDE SFM on its main thread, in Python 2.7 (print statement,
  no f-strings). SFM's UI freezes while code runs; keep calls short.
- Pre-imported: sfm, sfmApp, vs, sfmUtils, QtCore, QtGui, os, sys, json and
  H (helpers module, see sfm_api('H')). The namespace persists between calls.
- The value of the last expression is returned (like a REPL); print() output
  is returned too.
- Source units are inches, Z is up. Model paths look like
  'models/player/heavy.mdl' (use sfm_find_assets).
- sfm.* functions that use the "current" shot/animation set/selection need
  sfm_exec(context=True) (runs as an Animation Set Editor right-click script).
- Building a scene: sfm_create_model (models), sfm_light(create=True),
  sfm_camera(create=True); sfm_find_assets for paths. Put characters on the
  floor (check z against characters already standing there).
- Every Claude edit is one undo step: sfm_undo reverts mistakes.
- Final-quality pictures/videos: sfm_render (show=True returns the frame).
  sfm_screenshot is a quick viewport check. Backups: sfm_save_copy.
- Known crashers, never call from sfm_exec: sfmApp.ProcessEvents(),
  sfmApp.SetTimelineMode().
- Never overwrite or close the user's session without asking. Save your own
  work with a new file name.
- Moving only rootTransform makes a character slide in a frozen pose. For
  walking/running/idling use sfm_apply_animation with one of the model's
  animations (sfm_list_sequences), with root_motion for the travel.
  For motion the model has no clip for, key poses yourself with sfm_pose
  and check them with sfm_joints.
"""

mcp = MCPServer("sfm", instructions=INSTRUCTIONS)


# ------------------------------------------------------------ SFM paths
def _steam_libraries() -> list[Path]:
    libs: list[Path] = []
    steam = None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    if steam:
        libs.append(steam)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def sfm_game_dir() -> Path | None:
    env = os.environ.get("SFM_GAME_DIR")
    if env and Path(env, "sfm.exe").is_file():
        return Path(env)
    for lib in _steam_libraries():
        g = lib / "steamapps" / "common" / "SourceFilmmaker" / "game"
        if (g / "sfm.exe").is_file():
            return g
    return None


def _sfm_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq sfm.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return "sfm.exe" in out.lower()
    except Exception:
        return False


def _hook_installed() -> bool:
    g = sfm_game_dir()
    if not g:
        return False
    init = g / "usermod" / "scripts" / "sfm" / "sfm_init.py"
    return init.is_file() and HOOK_MARK in init.read_text(errors="ignore")


# --------------------------------------------------------- bridge client
class BridgeError(Exception):
    pass


async def bridge_call(cmd: str, args: dict | None = None, timeout: float = 60.0) -> dict:
    if not TOKEN_FILE.is_file():
        raise BridgeError(
            "No token file yet: the bridge has never started inside SFM. "
            "Is the hook installed (install_bridge.py) and SFM restarted?")
    token = TOKEN_FILE.read_text().strip()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT, limit=64 * 1024 * 1024), 5)
    except (OSError, asyncio.TimeoutError) as e:
        raise BridgeError(
            f"Cannot connect to the SFM bridge on {HOST}:{PORT} ({e}). "
            "SFM is not running, still loading, or the bridge did not start.") from None
    rid = uuid.uuid4().hex[:12]
    req = {"id": rid, "token": token, "cmd": cmd, "args": args or {}}
    try:
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except asyncio.TimeoutError:
        raise BridgeError(
            f"SFM did not answer within {timeout:.0f}s (the code may still be "
            "running and SFM's UI is frozen until it finishes).") from None
    finally:
        writer.close()
    if not line:
        raise BridgeError("Bridge closed the connection without a reply.")
    resp = json.loads(line)
    if resp.get("error") == "unauthorized":
        raise BridgeError("Bridge rejected the token (token file changed?). Restart SFM.")
    return resp


async def run_py(code: str, timeout: float = 60.0, undo: bool = True, label: str | None = None) -> dict:
    return await bridge_call("exec", {"code": code, "undo": undo, "label": label}, timeout)


def _round(o):
    """3 decimals are plenty for inches/degrees; also kills -0.0 and 1e-41."""
    if isinstance(o, float):
        r = round(o, 3)
        return 0.0 if r == 0 else r
    if isinstance(o, list):
        return [_round(x) for x in o]
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    return o


def _dump(o) -> str:
    """Compact JSON for tool output (tokens matter more than looks)."""
    return json.dumps(_round(o), ensure_ascii=False, separators=(",", ":"))


# Traceback noise: the bridge's own exec frame and long install paths.
_TB_BRIDGE = re.compile(r'  File "[^"]*sfm_mcp_bridge\.py"[^\n]*\n(?:    [^\n]*\n)?')
_TB_PATH = re.compile(r'[A-Za-z]:\\[^"\n]*?\\(?=(?:site-packages|sfm_side)\\)', re.I)


def _fmt(resp: dict) -> str:
    out = []
    if resp.get("stdout"):
        out.append(resp["stdout"].rstrip())
    if resp.get("ok"):
        r = resp.get("result")
        if r is not None:
            r = r if isinstance(r, str) else _dump(r)
            out.append(("=> " + r) if out else r)
        return "\n".join(out) or "ok"
    err = _TB_PATH.sub("", _TB_BRIDGE.sub("", resp.get("error") or "unknown error"))
    out.append("ERROR: " + err.rstrip())
    return "\n".join(out)


async def _helper(expr: str, timeout: float = 60.0) -> str:
    # Read-only helpers: no undo scope.
    try:
        return _fmt(await run_py(expr, timeout, undo=False))
    except BridgeError as e:
        return f"ERROR: {e}"


def _call_expr(fn: str, args: dict) -> str:
    args = {k: v for k, v in args.items() if v is not None and v != ""}
    return f"H.call({fn!r}, {json.dumps(json.dumps(args))})"


async def _ctx_exec(code: str, label: str, timeout: float = 60.0, animset: str = "") -> dict:
    """Run code inside SFM's animation-set script context (see ctx_start in
    sfm_mcp_scene.py): open the right-click menu, pick Rig > sfm_mcp_context."""
    r = await run_py(_call_expr("ctx_start", {"code": code, "label": label, "animset": animset}),
                     15, undo=False)
    if not r.get("ok"):
        if "no animation sets" in (r.get("error") or ""):
            # Empty shot: the Scripts-menu context is the best there is.
            return await bridge_call("exec_ctx", {"code": code, "undo": True, "label": label},
                                     timeout + 5)
        return r
    deadline = time.time() + timeout + 5
    while time.time() < deadline:
        await asyncio.sleep(0.2)
        p = await run_py("H.call('ctx_poll', '{}')", 15, undo=False)
        if not p.get("ok"):
            return p
        res = p.get("result") or {}
        if res.get("state") in ("menu", "running"):
            continue
        return res
    return {"ok": False, "error": f"context script did not finish within {timeout:.0f}s"}


# ----------------------------------------------------------------- tools
@mcp.tool(structured_output=False)
async def sfm_status() -> str:
    """Check SFM: install location, whether it is running, whether the bridge
    hook is installed and answering. Call this first."""
    g = sfm_game_dir()
    info = {
        "server": VERSION,
        "sfm_game_dir": str(g) if g else None,
        "sfm_running": _sfm_running(),
        "hook_installed": _hook_installed(),
        "token_present": TOKEN_FILE.is_file(),
        "port": PORT,
    }
    try:
        resp = await bridge_call("ping", timeout=5)
        info["bridge"] = resp.get("result")
    except BridgeError as e:
        info["bridge"] = None
        info["bridge_error"] = str(e)
    return _dump(info)


@mcp.tool(structured_output=False)
async def sfm_launch(wait_seconds: int = 0) -> str:
    """Start Source Filmmaker through Steam (if it is not running yet).
    wait_seconds > 0 waits up to that long for the bridge to answer."""
    if not _sfm_running():
        os.startfile("steam://rungameid/1840")
        msg = "Launch requested via Steam (steam://rungameid/1840)."
    else:
        msg = "SFM is already running."
    deadline = time.time() + max(0, wait_seconds)
    while True:
        try:
            r = await bridge_call("ping", timeout=3)
            return msg + " Bridge is up: " + json.dumps(r.get("result"))
        except BridgeError:
            if time.time() >= deadline:
                break
            await asyncio.sleep(3)
    return msg + " Bridge not answering yet (SFM takes a while to load)."


@mcp.tool(structured_output=False)
async def sfm_exec(code: str, context: bool = False, label: str = "", undo: bool = True,
                   timeout: float = 60.0, animset: str = "") -> str:
    """Execute Python 2.7 code inside SFM on its main thread and return the
    output. The last expression's value is returned (like a REPL).

    context=False: runs from the bridge timer. Fine for reading data and the
      DME element API (sfmApp.GetShots(), vs.*), but sfm.GetCurrentShot()
      etc. return None here.
    context=True: runs as an animation-set script (Animation Set Editor
      right-click > Rig), so sfm.GetCurrentShot(), GetCurrentAnimationSet(),
      CreateModel(), CreateAnimationSet() work. The menu flashes briefly.
      `animset` picks the "current" animation set (default: the first).

    Pre-imported: sfm, sfmApp, vs, sfmUtils, QtCore, QtGui, os, sys, json, H.
    The namespace persists between calls (shared by both modes).
    Python 2.7 syntax: `print x` works, f-strings do NOT.
    Do NOT call sfmApp.ProcessEvents() or sfmApp.SetTimelineMode() here:
    both crashed SFM in testing.

    Undo: each call runs inside one SFM undo step named `label` (shown in
    Edit > Undo, so the user can Ctrl+Z it). If the code raises, all its
    changes are rolled back. Changing DME data with undo=False triggers
    SFM's "Error in the undo system" dialog - keep undo=True.
    """
    label = label or "Claude: sfm_exec"
    try:
        if context:
            resp = await _ctx_exec(code, label, timeout, animset)
        else:
            resp = await run_py(code, timeout, undo=undo, label=label)
        return _fmt(resp)
    except BridgeError as e:
        return f"ERROR: {e}"


@mcp.tool(structured_output=False)
async def sfm_session_info() -> str:
    """Summary of the open SFM session: movie name, fps, playhead time,
    current shot, and for every shot its map, camera and animation sets."""
    return await _helper("H.session_info()")


@mcp.tool(structured_output=False)
async def sfm_screenshot(max_width: int = 768, frame: int | None = None,
                         viewport_only: bool = True, png: bool = False) -> Image | str:
    """Picture of SFM. viewport_only=True (default): just the primary
    viewport (the scene), cheapest in tokens; False: the whole window (UI,
    panels). frame=N first moves the playhead to shot frame N (to check
    animation). Sent as JPEG (~30-60 KB); png=True for lossless (5-10x
    bigger, only to read small UI text). Keep max_width ~500-900: cost
    grows with pixel area. For final-quality frames use sfm_render(show=True).
    Captures the window itself (Win32 PrintWindow): works even when other
    windows cover SFM, never shows other apps. SFM must not be minimized."""
    import wincapture

    hwnd = await asyncio.to_thread(wincapture.find_main_window)
    if not hwnd:
        return "ERROR: no visible SFM window found (is SFM running?)"
    crop = None
    try:
        if frame is not None:
            r = await run_py(f"H.call('set_frame', {json.dumps(json.dumps({'frame': frame}))})", 15,
                             undo=False)
            if not r.get("ok"):
                return _fmt(r)
            await asyncio.sleep(0.35)  # let SFM repaint the viewport
        if viewport_only:
            r = await run_py("H.call('viewport_rect', '{}')", 10, undo=False)
            crop = r.get("result") if r.get("ok") else None
        data = await asyncio.to_thread(wincapture.capture_window, hwnd, int(max_width), crop,
                                       "png" if png else "jpeg")
    except BridgeError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR: {e}"
    return Image(data=data, format="png" if png else "jpeg")


@mcp.tool(structured_output=False)
async def sfm_set_frame(frame: int, shot: str = "") -> str:
    """Move the playhead to a shot frame (0 = shot start)."""
    return await _scene("set_frame", False, frame=frame, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_api(name: str = "sfm", filter: str = "", docs: bool = False) -> str:
    """Explore SFM's Python API live: dir() of a module/object, optionally
    filtered by substring, with first doc lines when docs=True.
    name examples: 'sfm', 'sfmApp', 'vs', 'vs.movieobjects', 'sfmUtils', 'H'."""
    return await _helper(f"H.api({name!r}, filter={filter or None!r}, docs={bool(docs)!r})")


@mcp.tool(structured_output=False)
async def sfm_console(command: str) -> str:
    """Run an SFM engine console command (sfmApp.ExecuteGameCommand)."""
    return await _helper(f"H.console({command!r})")


@mcp.tool(structured_output=False)
async def sfm_reload_helpers() -> str:
    """Reload sfm_side/sfm_mcp_helpers.py inside SFM after editing it
    (no SFM restart needed)."""
    try:
        return _fmt(await bridge_call("reload", timeout=30))
    except BridgeError as e:
        return f"ERROR: {e}"


# ------------------------------------------------------------ scene tools
# Thin wrappers over sfm_side/sfm_mcp_scene.py (DME layer, no script context
# needed). Frames are relative to the shot start; rotations are Source
# [pitch, yaw, roll] degrees; units are inches, Z up.
async def _scene(fn: str, undo: bool, label: str | None = None, **kwargs) -> str:
    args = {k: v for k, v in kwargs.items() if v is not None and v != ""}
    code = f"H.call({fn!r}, {json.dumps(args)!r})"
    try:
        return _fmt(await run_py(code, 60, undo=undo, label=label))
    except BridgeError as e:
        return f"ERROR: {e}"


@mcp.tool(structured_output=False)
async def sfm_list_animsets(shot: str = "") -> str:
    """Animation sets in a shot (models, cameras, lights) with model path and
    root position/rotation at the playhead. shot='' = shot under playhead."""
    return await _scene("animsets", False, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_list_controls(animset: str, filter: str = "", shot: str = "") -> str:
    """Controls of an animation set: bones/rootTransform are [transform],
    face/flex sliders show their value (stereo = left/right)."""
    return await _scene("controls", False, animset=animset, filter=filter, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_get_transform(animset: str, control: str = "rootTransform",
                            frame: int | None = None, shot: str = "") -> str:
    """Position [x,y,z] and rotation [pitch,yaw,roll] of a transform control
    (rootTransform or a bone like bip_head) at a frame (default playhead)."""
    return await _scene("get_transform", False, animset=animset, control=control,
                        frame=frame, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_set_transform(animset: str, position: list[float] | None = None,
                            rotation: list[float] | None = None, control: str = "rootTransform",
                            frame: int | None = None, relative: bool = False, shot: str = "") -> str:
    """Move/rotate a model, bone or any transform control.
    frame=None: static placement (if not animated yet). frame=N: keyframe at
    shot frame N (use several calls for an animation). relative=True adds to
    the current value. One undo step."""
    return await _scene("set_transform", True, f"Claude: transform {animset}.{control}",
                        animset=animset, position=position, rotation=rotation, control=control,
                        frame=frame, relative=relative, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_set_control(animset: str, control: str, value: float | None = None,
                          left: float | None = None, right: float | None = None,
                          frame: int | None = None, shot: str = "") -> str:
    """Set a slider control (facial flex like Smile, blink, etc.; usually 0..1).
    Stereo controls: value sets both sides, or pass left/right.
    frame=None: static; frame=N: keyframe. One undo step."""
    return await _scene("set_control", True, f"Claude: {animset}.{control}", animset=animset,
                        control=control, value=value, left=left, right=right, frame=frame, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_get_keys(animset: str, control: str = "rootTransform", shot: str = "") -> str:
    """Keyframe frames of a control (negative = SFM's pre-roll handle)."""
    return await _scene("keys", False, animset=animset, control=control, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_list_sequences(animset: str, filter: str = "", shot: str = "") -> str:
    """Animations built into a model (run, idle, taunts, gestures...).
    With a filter, blend sequences (e.g. TF2 run_MELEE) also list the single
    animations inside them (a_runN_MELEE = forward, S = backward, E/W =
    sideways), which sfm_apply_animation accepts too."""
    return await _scene("sequences", False, animset=animset, filter=filter, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_apply_animation(animset: str, animation: str, start_frame: int = 0,
                              end_frame: int | None = None, loops: float | None = None,
                              rate: float = 1.0, root_motion: bool = True, blend: int = 2,
                              shot: str = "") -> str:
    """Make a model really walk/run/idle/taunt: bakes one of its built-in
    animations (see sfm_list_sequences) onto all bones as keyframes from
    start_frame to end_frame (or for `loops` passes; looping animations
    repeat). root_motion=True also moves the model along its facing at the
    animation's own ground speed so the feet don't slide; set the facing
    first with sfm_set_transform(rotation=...). blend = frames to blend
    in/out from the previous pose. Replaces bone keys in that range.
    One undo step."""
    return await _scene("apply_animation", True, f"Claude: {animation} on {animset}",
                        animset=animset, animation=animation, start_frame=start_frame,
                        end_frame=end_frame, loops=loops, rate=rate, root_motion=root_motion,
                        blend=blend, shot=shot)


_POSE_SPEC = """Pose spec (all in the CHARACTER's axes: X forward, Y left, Z up;
bone names accept * wildcards, e.g. "bip_index_*_R"):
  reset [bones]            back to the model's reference pose (open hand).
  move {bone: [dx,dy,dz]}  offset in inches (bip_pelvis [0,0,-8] = crouch).
  rotate {bone: [rx,ry,rz]} degrees added to the pose: ry>0 bends forward/
      down, rz>0 turns left, rx>0 tilts upward-pointing bones to the
      character's right (a hanging arm swings left).
  aim {bone: [x,y,z] | "parent"}  point the bone at its child joint
      (bip_upperArm_R [0.2,-0.8,0.55] = arm up and out; "parent" =
      straight in line with the parent bone).
  twist {bone: deg}        roll around the bone's own axis (forearm twist
      turns the palm).
Applied parents first, per bone in the order above."""


async def sfm_animate(animset: str, keys: list[dict], ease: str = "smooth",
                      report: list[str] | None = None, shot: str = "") -> str:
    """Own hand-made animation (no stored clips) as an additive LAYER: the
    existing motion (idle breathing etc.) keeps playing underneath, so the
    result looks alive. keys: [{"frame": f, ...pose spec..., "ease":
    "smooth"|"linear"}] (ease = curve of the segment ending at that key).
    Put a rest key {"frame": f} at the start and end. Every key should list
    ALL bones that are posed at that moment (parents included), like an
    animator's key pose. For natural motion: anticipation before big moves,
    ease in/out, overlap (hand lags the forearm), open hands for gestures,
    keep the body alive (use sfm_apply_animation for an idle underneath),
    dense 'linear' keys from a sine for cycles. Bakes a key per frame.
    One undo step. report: bones whose joint positions are returned per key.
    """
    return await _scene("animate", True, f"Claude: animate {animset}", animset=animset,
                        keys=keys, ease=ease, report=report, shot=shot)


async def sfm_pose(animset: str, frame: int | None = None, rotate: dict | None = None,
                   aim: dict | None = None, move: dict | None = None, twist: dict | None = None,
                   reset: list[str] | None = None, report: list[str] | None = None,
                   shot: str = "") -> str:
    """Key one absolute pose at one frame (many bones, one undo step).
    For motion over time use sfm_animate (smooth, layered). Returns joint
    positions (inches) to check the pose without a screenshot.
    """
    return await _scene("pose", True, f"Claude: pose {animset}", animset=animset, frame=frame,
                        rotate=rotate, aim=aim, move=move, twist=twist, reset=reset,
                        report=report, shot=shot)


for _f in (sfm_animate, sfm_pose):  # docstrings share the pose spec
    _f.__doc__ = _f.__doc__.rstrip() + "\n\n" + _POSE_SPEC
    mcp.tool(structured_output=False)(_f)


@mcp.tool(structured_output=False)
async def sfm_joints(animset: str, bones: list[str], frame: int | None = None,
                     shot: str = "") -> str:
    """Character-space joint positions (inches; X forward, Y left, Z up) of
    bones at a frame: a cheap numeric check of a pose (feet on the ground,
    hand above head...)."""
    return await _scene("joints", False, animset=animset, bones=bones, frame=frame, shot=shot)


async def _new_animset(kind: str) -> tuple[str, str]:
    """Queue SFM's Animation Set Editor 'Add' command and wait for the new
    animation set. Returns (name, error)."""
    r = await run_py(_call_expr("add_element", {"kind": kind}), 15, undo=False)
    if not r.get("ok"):
        return "", _fmt(r)
    existing = set(r["result"]["existing"])
    for _ in range(24):
        await asyncio.sleep(0.25)
        r = await run_py("[str(a.GetName()) for a in H.S._shot().animationSets]", 10, undo=False)
        new = [n for n in (r.get("result") or []) if n not in existing]
        if new:
            return new[-1], ""
    return "", f"ERROR: SFM did not create the {kind} (is the Animation Set Editor open?)"


@mcp.tool(structured_output=False)
async def sfm_camera(animset: str = "", create: bool = False, name: str = "",
                     position: list[float] | None = None, rotation: list[float] | None = None,
                     look_at: list[float] | None = None, fov: float | None = None,
                     exposure: float | None = None, focal_distance: float | None = None,
                     active: bool | None = None, shot: str = "") -> str:
    """Get or set a camera; create=True adds a new one (SFM's own 'New
    Camera' command, placed at the work camera) and makes it the shot
    camera unless active=False. animset='' = the shot's active camera.
    look_at [x,y,z] aims it; fov degrees 10-120; exposure = toneMapScale
    (1 default; lower = darker, fixes overexposure); focal_distance inches
    (depth of field); active=True switches the shot to this camera; name
    renames it. No arguments: report the active camera."""
    try:
        if create:
            animset, err = await _new_animset("camera")
            if err:
                return err
            if active is None:
                active = True
    except BridgeError as e:
        return f"ERROR: {e}"
    setting = create or any(v not in (None, "") for v in (
        name, position, rotation, look_at, fov, exposure, focal_distance, active))
    return await _scene("camera", setting, f"Claude: camera {name or animset}" if setting else None,
                        animset=animset, name=name, position=position, rotation=rotation,
                        look_at=look_at, fov=fov, exposure=exposure,
                        focal_distance=focal_distance, active=active, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_light(animset: str = "", create: bool = False, name: str = "",
                    position: list[float] | None = None, rotation: list[float] | None = None,
                    look_at: list[float] | None = None, color: list[float] | None = None,
                    intensity: float | None = None, fov: float | None = None,
                    distance: float | None = None, softness: float | None = None,
                    shadows: bool | None = None, volumetric: bool | None = None,
                    volumetric_intensity: float | None = None, texture: str = "",
                    frame: int | None = None, shot: str = "") -> str:
    """Create (create=True, via SFM's own 'New Light' command) and/or set a
    projected light. animset='' = the last light. color [r,g,b] 0-255;
    intensity 0-1000 (SFM default 500); fov 10-120 deg; distance = reach in
    inches (60-3000); softness = shadow blur 0-50; shadows/volumetric on/off;
    texture e.g. 'effects/flashlight001'; look_at [x,y,z] aims it; name
    renames it. frame=N keys the change (animated light), else static.
    No arguments: report the light."""
    if create:
        try:
            animset, err = await _new_animset("light")
        except BridgeError as e:
            return f"ERROR: {e}"
        if err:
            return err
    setting = create or any(v not in (None, "") for v in (
        name, position, rotation, look_at, color, intensity, fov, distance, softness, shadows,
        volumetric, volumetric_intensity, texture))
    return await _scene("light", setting, f"Claude: light {name or animset}" if setting else None,
                        animset=animset, name=name, position=position, rotation=rotation,
                        look_at=look_at, color=color, intensity=intensity, fov=fov,
                        distance=distance, softness=softness, shadows=shadows,
                        volumetric=volumetric, volumetric_intensity=volumetric_intensity,
                        texture=texture, frame=frame, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_create_model(model: str, name: str = "", position: list[float] | None = None,
                           rotation: list[float] | None = None, skin: int | None = None) -> str:
    """Add a model (.mdl) to the shot as a new animation set, like Animation
    Set Editor > Add > Create Animation Set for New Model. model: path from
    sfm_find_assets ('models/player/hwm/heavy.mdl'; 'models/' and '.mdl'
    may be left out). name defaults to <file name>1, <file name>2...
    position [x,y,z] / rotation [pitch,yaw,roll] place it (z = floor
    height: compare with characters already standing in the scene).
    Needs at least one animation set in the shot (the right-click menu
    of the Animation Set Editor is used; it flashes briefly). One undo step."""
    code = _call_expr("create_model", {"model": model, "name": name, "position": position,
                                       "rotation": rotation, "skin": skin})
    try:
        return _fmt(await _ctx_exec(code, f"Claude: create model {name or model}", 60))
    except BridgeError as e:
        return f"ERROR: {e}"


@mcp.tool(structured_output=False)
async def sfm_shift(animset: str, offset: list[float], control: str = "rootTransform",
                    shot: str = "") -> str:
    """Move a control's WHOLE position animation by offset [dx,dy,dz] inches
    (every key), e.g. put a floating character on the floor or move a
    baked walk elsewhere. One undo step."""
    return await _scene("shift", True, f"Claude: shift {animset}", animset=animset,
                        offset=offset, control=control, shot=shot)


@mcp.tool(structured_output=False)
async def sfm_undo(steps: int = 1, redo: bool = False) -> str:
    """Undo (or redo) the last SFM edit steps, exactly like Ctrl+Z /
    Ctrl+Shift+Z. Every Claude edit is one named step. Returns the step
    names before and after."""
    try:
        r = await run_py(_call_expr("undo", {"steps": steps, "redo": redo}), 15, undo=False)
        if not r.get("ok"):
            return _fmt(r)
        await asyncio.sleep(0.3 + 0.05 * steps)
        st = await run_py("H.call('undo_state', '{}')", 15, undo=False)
        return _dump({"done": r.get("result"), "now": st.get("result")})
    except BridgeError as e:
        return f"ERROR: {e}"


@mcp.tool(structured_output=False)
async def sfm_save_copy(path: str, overwrite: bool = False) -> str:
    """Save a COPY of the open session to a new .dmx (e.g. a backup before
    big changes, or Claude's own version). The open document, its file name
    and its unsaved state stay untouched; the user's own session file is
    never written (they save it with Ctrl+S)."""
    return await _scene("save_copy", False, None, path=path, overwrite=overwrite)


def _default_render_path(kind: str) -> Path:
    videos = Path.home() / "Videos"
    base = (videos if videos.is_dir() else Path.home()) / "sfm-mcp"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return base / stamp / ("frame" if kind == "images" else f"render_{stamp}")


@mcp.tool(structured_output=False)
async def sfm_render(path: str = "", kind: str = "images", format: str = "",
                     codec: str = "", width: int | None = None, height: int | None = None,
                     start: int | None = None, end: int | None = None,
                     dof: bool | None = None, motion_blur: bool | None = None,
                     antialias: bool | None = None, ambient_occlusion: bool | None = None,
                     audio: bool | None = None, show: bool = False,
                     timeout: float = 900) -> list | str:
    """Render with SFM's own exporter (File > Export > Movie...), final
    quality. kind: images (PNG/JPEG/TGA sequence) | movie (MP4/MOV/AVI;
    codec MPEG4/H.264/Uncompressed) | sound. path: output base without
    extension (default ~/Videos/sfm-mcp/<time>/frame); images are written
    as <path>000123.png. start/end: shot frames, end inclusive (a single
    still: start=end=N). width/height in pixels. dof/motion_blur/antialias/
    ambient_occlusion switch those passes (off = much faster previews).
    show=True returns the last rendered image (JPEG, 768 px) for checking.
    The document is not saved: SFM's "save changes?" question is answered
    "Continue without Saving"; the export settings are remembered in it."""
    import glob

    if kind not in ("images", "movie", "sound"):
        return "ERROR: kind must be images, movie or sound"
    out = Path(path) if path else _default_render_path(kind)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = {"path": str(out), "kind": kind, "fmt": format, "codec": codec, "width": width,
            "height": height, "start": start, "end": end, "dof": dof,
            "motion_blur": motion_blur, "antialias": antialias,
            "ambient_occlusion": ambient_occlusion, "audio": audio}
    try:
        head = await run_py("H.S.head_frame()", 10, undo=False)
        r = await run_py(_call_expr("render_start", args), 15, undo=False)
        if not r.get("ok"):
            return _fmt(r)
        t0 = time.time()
        state = {}
        while time.time() - t0 < timeout:
            await asyncio.sleep(1.0)
            try:
                p = await run_py("H.call('render_poll', '{}')", 30, undo=False)
            except BridgeError:
                continue  # SFM busy rendering
            state = p.get("result") or {}
            if state.get("state") in ("done", "error"):
                break
        if head.get("ok") and head.get("result") is not None:
            await run_py(_call_expr("set_frame", {"frame": head["result"]}), 10, undo=False)
    except BridgeError as e:
        return f"ERROR: {e}"
    if state.get("state") == "error":
        return "ERROR: " + str(state.get("error"))
    files = sorted(f for f in glob.glob(str(out) + "*")
                   if not f.lower().endswith((".txt", ".wav")) or kind == "sound")
    if state.get("state") != "done":
        return f"ERROR: render still running after {timeout:.0f}s; {len(files)} file(s) so far"
    size = sum(os.path.getsize(f) for f in files)
    summary = _dump({"files": len(files), "first": files[0] if files else None,
                     "last": files[-1] if len(files) > 1 else None,
                     "mb": round(size / 1e6, 2), "seconds": round(time.time() - t0, 1),
                     "settings": state.get("settings")})
    if show and files and files[-1].lower().endswith((".png", ".jpg", ".jpeg", ".tga")):
        import wincapture
        data = await asyncio.to_thread(wincapture.shrink_image, files[-1], 768)
        return [summary, Image(data=data, format="jpeg")]
    return summary


# ---------------------------------------------------------- asset search
_KINDS = {
    "models": ("models", (".mdl",)),
    "maps": ("maps", (".bsp",)),
    "sounds": ("sound", (".wav", ".mp3")),
    "particles": ("particles", (".pcf",)),
    "materials": ("materials", (".vmt",)),
    "sessions": ("elements/sessions", (".dmx",)),
}
_asset_cache: dict[str, list[tuple[str, str]]] = {}


def _index(kind: str) -> list[tuple[str, str]]:
    if kind in _asset_cache:
        return _asset_cache[kind]
    g = sfm_game_dir()
    sub, exts = _KINDS[kind]
    rows: list[tuple[str, str]] = []
    if g:
        for mod in sorted(p for p in g.iterdir() if p.is_dir()):
            base = mod / sub
            if not base.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(base):
                for f in files:
                    if f.lower().endswith(exts):
                        rel = os.path.relpath(os.path.join(dirpath, f), mod).replace("\\", "/")
                        rows.append((mod.name, rel))
    _asset_cache[kind] = rows
    return rows


@mcp.tool(structured_output=False)
def sfm_find_assets(query: str = "", kind: str = "models", limit: int = 25, refresh: bool = False) -> str:
    """Search SFM content on disk (all mods: tf, hl2, usermod, workshop...).
    kind: models | maps | sounds | particles | materials | sessions.
    query: space-separated words that must all appear in the path.
    Returns 'mod: path' lines; the path is what SFM expects
    (e.g. 'models/player/heavy.mdl')."""
    if kind not in _KINDS:
        return f"ERROR: kind must be one of {', '.join(_KINDS)}"
    if refresh:
        _asset_cache.pop(kind, None)
    rows = _index(kind)
    words = [w.lower() for w in query.split()]
    hits = [r for r in rows if all(w in r[1].lower() for w in words)]
    # Rank: file name equal to a query word, then words found in the file
    # name, then shorter paths.
    def _rank(r):
        base = r[1].lower().rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        return (-(stem in words), -sum(w in base for w in words), len(r[1]), r[1])

    hits.sort(key=_rank)
    out = [f"{len(hits)} match(es) of {len(rows)} {kind}"]
    out += [f"{m}: {p}" for m, p in hits[: max(1, limit)]]
    if len(hits) > limit:
        out.append(f"... {len(hits) - limit} more, refine the query")
    return "\n".join(out)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
