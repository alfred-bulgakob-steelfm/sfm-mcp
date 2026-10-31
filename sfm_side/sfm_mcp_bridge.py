# -*- coding: utf-8 -*-
"""sfm-mcp bridge: runs INSIDE Source Filmmaker (embedded Python 2.7).

A tiny TCP server on 127.0.0.1 that executes Python code on SFM's main thread
and returns the result. The MCP server (Python 3, outside SFM) talks to it.

Design notes
- SFM's API is not thread-safe, so there are NO threads here. A QTimer on the
  main thread polls a non-blocking socket every few milliseconds.
- Every request must carry the token stored in %USERPROFILE%\\.sfm_mcp\\token.
- Protocol: one JSON object per line (UTF-8), one response line per request.
    request : {"id": .., "token": "..", "cmd": "ping"|"exec"|"reload", "args": {..}}
    response: {"id": .., "ok": true|false, "result": .., "stdout": "..", "error": ".."}
- Can also run outside SFM for testing:  python.exe sfm_mcp_bridge.py --standalone
"""
import os
import sys
import json
import time
import socket
import errno
import traceback
import ast

try:
    from cStringIO import StringIO
except ImportError:  # pragma: no cover
    from io import StringIO

BRIDGE_VERSION = "0.3.0"
HOST = "127.0.0.1"
PORT = int(os.environ.get("SFM_MCP_PORT", "9877"))
TICK_MS = 15
MAX_REQUEST = 16 * 1024 * 1024
MAX_TEXT = 200000

HERE = os.path.dirname(os.path.abspath(__file__))
CONF_DIR = os.path.join(os.path.expanduser("~"), ".sfm_mcp")
TOKEN_FILE = os.path.join(CONF_DIR, "token")
STATUS_FILE = os.path.join(CONF_DIR, "bridge_status.json")

_state = {
    "listener": None,
    "clients": {},      # socket -> bytes buffer
    "timer": None,
    "ns": None,         # persistent exec namespace
    "token": None,
    "started": None,
    "requests": 0,
}


def log(msg):
    try:
        print "[sfm-mcp] %s" % msg
    except Exception:
        pass


# ----------------------------------------------------------------- token
def _load_token():
    if not os.path.isdir(CONF_DIR):
        os.makedirs(CONF_DIR)
    if os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            tok = f.read().strip()
        if len(tok) >= 32:
            return tok
    tok = os.urandom(24).encode("hex")
    with open(TOKEN_FILE, "w") as f:
        f.write(tok)
    return tok


def _consteq(a, b):
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= ord(x) ^ ord(y)
    return r == 0


# ------------------------------------------------------------- namespace
def _make_namespace():
    ns = {"__name__": "__sfm_mcp__", "__builtins__": __builtins__}
    for mod in ("sfm", "sfmApp", "vs", "sfmClipEditor"):
        try:
            ns[mod] = __import__(mod)
        except Exception:
            pass
    try:
        import sfmUtils
        ns["sfmUtils"] = sfmUtils
    except Exception:
        try:
            from sfm import sfmUtils  # noqa
            ns["sfmUtils"] = sfmUtils
        except Exception:
            pass
    try:
        from PySide import QtCore, QtGui
        ns["QtCore"] = QtCore
        ns["QtGui"] = QtGui
    except Exception:
        pass
    ns["os"] = os
    ns["sys"] = sys
    ns["json"] = json
    _load_helpers(ns)
    return ns


def _load_helpers(ns, do_reload=False):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import sfm_mcp_helpers
    if do_reload:
        sfm_mcp_helpers = reload(sfm_mcp_helpers)
    sfm_mcp_helpers.init(ns)
    ns["H"] = sfm_mcp_helpers
    return sfm_mcp_helpers


# ------------------------------------------------------------------ exec
def _jsonable(v, depth=0):
    if v is None or isinstance(v, (bool, int, long, float)):
        return v
    if isinstance(v, str):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin-1")
    if isinstance(v, unicode):
        return v
    if depth < 6 and isinstance(v, (list, tuple)):
        return [_jsonable(x, depth + 1) for x in v]
    if depth < 6 and isinstance(v, dict):
        return dict((unicode(k), _jsonable(x, depth + 1)) for k, x in v.items())
    try:
        return u"<%s> %s" % (type(v).__name__, repr(v)[:2000])
    except Exception:
        return u"<%s>" % type(v).__name__


def _clip(s):
    if s and len(s) > MAX_TEXT:
        return s[:MAX_TEXT] + "\n...[truncated %d chars]" % (len(s) - MAX_TEXT)
    return s


def _begin_undo(label):
    """Open an SFM undo scope. Changing DME elements outside one triggers
    SFM's 'Error in the undo system!' dialog. One exec = one undo step the
    user can Ctrl+Z; on error the scope is aborted (changes rolled back)."""
    if "vs" not in sys.modules:  # standalone test mode
        return None
    try:
        import vs
        flags = vs.NOTIFY_SETDIRTYFLAG | vs.NOTIFY_CHANGE_MASK
        return vs.CAppUndoScopeGuard(flags, str(label or "sfm-mcp")[:120])
    except Exception:
        log("undo scope failed: %s" % traceback.format_exc(limit=1))
        return None


def _exec_code(code, undo=True, label=None):
    ns = _state["ns"]
    if ns is None:
        ns = _state["ns"] = _make_namespace()
    guard = _begin_undo(label) if undo else None
    # Compile from unicode so u'...' literals with non-ASCII text survive
    # (a byte string would be read as latin-1 by Python 2).
    if isinstance(code, str):
        code = code.decode("utf-8", "replace")
    code = code.replace(u"\r\n", u"\n")
    out = StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = out
    result = None
    try:
        tree = ast.parse(code, "<sfm-mcp>", "exec")
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body.pop()
        if tree.body:
            exec compile(tree, "<sfm-mcp>", "exec") in ns
        if last is not None:
            expr = ast.Expression(last.value)
            result = eval(compile(expr, "<sfm-mcp>", "eval"), ns)
        ok, err = True, None
    except Exception:
        ok, err = False, traceback.format_exc()
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        if guard is not None:
            try:
                if ok:
                    guard.Release()
                else:
                    guard.Abort()
                    err = (err or "") + "\n[sfm-mcp] changes from this call were rolled back (undo scope aborted)"
            except Exception:
                err = (err or "") + "\n[sfm-mcp] undo scope close failed: " + traceback.format_exc(limit=1)
    text = out.getvalue()
    try:
        text = text.decode("utf-8")
    except Exception:
        text = text.decode("latin-1")
    resp = {"ok": ok, "result": _jsonable(result), "stdout": _clip(text)}
    if err:
        resp["error"] = _clip(err.decode("utf-8", "replace"))
    return resp


# ------------------------------------------------------- script context
# Scripts started from SFM's Scripts menu get a context (current shot,
# animation set, selection). Bridge code runs from a QTimer and has none.
# "exec_ctx" queues the code, then asks Qt to trigger our own menu entry
# (mainmenu/sfm-mcp/sfm_mcp_context.py) from the event loop, exactly like a
# user click. NEVER call the action synchronously from bridge code: SFM's
# script runner swaps Python state and returning into a live bridge frame
# crashed SFM (access violation in python27.dll).
CTX_TIMEOUT = 120.0
_ctx = {"job": None}


def _find_context_action():
    import sfmApp
    menu = sfmApp.GetScriptsMenu()
    for a in menu.actions():
        sub = a.menu()
        t = (a.text() or "").lower()
        if sub is None or "sfm" not in t or "mcp" not in t:
            continue
        for b in sub.actions():
            if "context" in (b.text() or "").lower():
                return b
    return None


def _start_ctx_job(sock, rid, code, undo=True, label=None):
    if _ctx["job"] is not None:
        return {"ok": False, "error": "another context job is still running"}
    act = _find_context_action()
    if act is None:
        return {"ok": False, "error": "sfm-mcp context script is not in the "
                "Scripts menu; run install_bridge.py and restart SFM"}
    _ctx["job"] = {"sock": sock, "id": rid, "code": code, "t0": time.time(),
                   "undo": undo, "label": label,
                   "started": False, "resp": None}
    from PySide import QtCore
    QtCore.QMetaObject.invokeMethod(act, "trigger", QtCore.Qt.QueuedConnection)
    return None  # deferred: answered from tick when the menu script ran


def run_pending():
    """Called by the Scripts-menu entry, inside SFM's script context."""
    job = _ctx["job"]
    if job is None or job["started"]:
        log("context script: nothing pending")
        return
    job["started"] = True
    _state["busy"] = True  # keep bridge ticks out while user code runs
    try:
        job["resp"] = _exec_code(job["code"], job["undo"], job["label"])
    finally:
        _state["busy"] = False


def _finish_ctx_job():
    job = _ctx["job"]
    if job is None:
        return
    resp = job["resp"]
    if resp is None:
        if time.time() - job["t0"] < CTX_TIMEOUT:
            return
        resp = {"ok": False, "error": "context script did not run within %ds"
                % CTX_TIMEOUT, "started": job["started"]}
    _ctx["job"] = None
    resp["id"] = job["id"]
    resp["context"] = True
    try:
        _send(job["sock"], resp)
    except Exception:
        _close(job["sock"])


def handle(req, sock=None):
    cmd = req.get("cmd")
    args = req.get("args") or {}
    if cmd == "exec_ctx":
        return _start_ctx_job(sock, req.get("id"), args.get("code", ""),
                              args.get("undo", True), args.get("label"))
    if cmd == "ping":
        ns = _state["ns"]
        return {"ok": True, "result": {
            "bridge": BRIDGE_VERSION,
            "python": sys.version,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - (_state["started"] or time.time()), 1),
            "requests": _state["requests"],
            "in_sfm": "sfmApp" in sys.modules,
            "namespace_ready": ns is not None,
        }}
    if cmd == "exec":
        return _exec_code(args.get("code", ""), args.get("undo", True),
                          args.get("label"))
    if cmd == "reload":
        try:
            if _state["ns"] is None:
                _state["ns"] = _make_namespace()
            else:
                _load_helpers(_state["ns"], do_reload=True)
            return {"ok": True, "result": "helpers reloaded"}
        except Exception:
            return {"ok": False, "error": traceback.format_exc()}
    if cmd == "reset":
        _state["ns"] = None
        return {"ok": True, "result": "namespace cleared"}
    return {"ok": False, "error": "unknown cmd: %r" % (cmd,)}


# --------------------------------------------------------------- network
def _send(sock, obj):
    data = json.dumps(obj, ensure_ascii=True) + "\n"
    try:
        sock.setblocking(True)
        sock.settimeout(10.0)
        sock.sendall(data)
    finally:
        try:
            sock.setblocking(False)
        except Exception:
            pass


def _close(sock):
    _state["clients"].pop(sock, None)
    try:
        sock.close()
    except Exception:
        pass


def _process_line(sock, line):
    try:
        req = json.loads(line)
    except Exception:
        _send(sock, {"ok": False, "error": "bad json"})
        return
    rid = req.get("id")
    tok = req.get("token")
    if not isinstance(tok, basestring) or not _consteq(str(tok), _state["token"]):
        _send(sock, {"id": rid, "ok": False, "error": "unauthorized"})
        _close(sock)
        return
    _state["requests"] += 1
    try:
        resp = handle(req, sock)
    except Exception:
        resp = {"ok": False, "error": traceback.format_exc()}
    if resp is None:  # deferred (exec_ctx)
        return
    resp["id"] = rid
    try:
        _send(sock, resp)
    except Exception:
        _close(sock)


def tick():
    # Re-entrancy guard: code run by a request may pump the Qt event loop
    # (processEvents, modal dialogs, map loads). The timer would then fire
    # again *inside* the request; handling sockets there corrupts state and
    # can take SFM down. Ignore nested ticks.
    if _state.get("busy"):
        return
    _state["busy"] = True
    try:
        _tick_body()
    finally:
        _state["busy"] = False


def _tick_body():
    lst = _state["listener"]
    if lst is None:
        return
    _finish_ctx_job()
    # accept new connections
    for _ in range(8):
        try:
            c, addr = lst.accept()
        except socket.error as e:
            if e.args and e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN, 10035):
                break
            break
        if addr[0] != "127.0.0.1":
            c.close()
            continue
        c.setblocking(False)
        _state["clients"][c] = ""
    # read from clients
    for c in list(_state["clients"].keys()):
        try:
            chunk = c.recv(1 << 16)
        except socket.error as e:
            if e.args and e.args[0] in (errno.EWOULDBLOCK, errno.EAGAIN, 10035):
                continue
            _close(c)
            continue
        if not chunk:
            _close(c)
            continue
        buf = _state["clients"].get(c, "") + chunk
        if len(buf) > MAX_REQUEST:
            _close(c)
            continue
        while c in _state["clients"] and "\n" in buf:
            line, buf = buf.split("\n", 1)
            if line.strip():
                _process_line(c, line)
        if c in _state["clients"]:
            _state["clients"][c] = buf


def _tick_safe():
    try:
        tick()
    except Exception:
        log("tick error: %s" % traceback.format_exc(limit=3))


def _write_status(extra=None):
    st = {"bridge": BRIDGE_VERSION, "port": PORT, "pid": os.getpid(),
          "started": _state["started"], "python": sys.version}
    if extra:
        st.update(extra)
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(st, f, indent=2)
    except Exception:
        pass


def _open_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows: refuse to share the port with another process.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind((HOST, PORT))
    s.listen(8)
    s.setblocking(False)
    return s


def start():
    """Start the bridge inside SFM (idempotent)."""
    if _state["listener"] is not None:
        log("already running on %s:%d" % (HOST, PORT))
        return True
    try:
        _state["token"] = _load_token()
        _state["listener"] = _open_listener()
        _state["started"] = time.time()
    except Exception:
        log("could not start: %s" % traceback.format_exc(limit=2))
        _write_status({"error": traceback.format_exc(limit=2)})
        return False
    from PySide import QtCore
    t = QtCore.QTimer()
    t.timeout.connect(_tick_safe)
    t.start(TICK_MS)
    _state["timer"] = t  # keep a reference so it is not garbage collected
    _write_status()
    log("bridge %s listening on %s:%d" % (BRIDGE_VERSION, HOST, PORT))
    return True


def stop():
    t = _state.get("timer")
    if t is not None:
        try:
            t.stop()
        except Exception:
            pass
    _state["timer"] = None
    for c in list(_state["clients"].keys()):
        _close(c)
    if _state["listener"] is not None:
        try:
            _state["listener"].close()
        except Exception:
            pass
    _state["listener"] = None
    log("bridge stopped")


def _standalone():
    """Test mode outside SFM: same code path, plain loop instead of QTimer."""
    _state["token"] = _load_token()
    _state["listener"] = _open_listener()
    _state["started"] = time.time()
    log("standalone bridge on %s:%d (Ctrl+C to stop)" % (HOST, PORT))
    try:
        while True:
            tick()
            time.sleep(TICK_MS / 1000.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__" and "--standalone" in sys.argv:
    _standalone()
