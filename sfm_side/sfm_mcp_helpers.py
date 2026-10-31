# -*- coding: utf-8 -*-
"""Helpers available as `H` inside sfm-mcp exec calls (Python 2.7, inside SFM).

This module is reloadable at runtime (bridge cmd "reload"), so new helpers can
be added without restarting SFM. Keep it Python 2.7 compatible.
Every helper returns plain JSON-able data (dict/list/str/number).
"""
import os
import sys
import time

NS = {}


def init(ns):
    # Keep a reference (not a copy): names defined by exec calls, and H
    # itself, stay visible to the helpers.
    global NS
    NS = ns


def _mod(name):
    m = NS.get(name)
    if m is None:
        m = sys.modules.get(name)
    return m


def _try(fn, default=None):
    try:
        return fn()
    except Exception as e:
        if default is not None:
            return default
        return "<error: %s>" % str(e)[:200]


def _s(v):
    try:
        return str(v)
    except Exception:
        try:
            return unicode(v)
        except Exception:
            return repr(v)


# ------------------------------------------------------------ discovery
def api(name="sfm", filter=None, docs=False, limit=400):
    """dir() of a module/object reachable from the exec namespace.
    name: dotted path, e.g. 'sfm', 'sfmApp', 'vs.movieobjects', 'sfm.CreateModel'."""
    parts = name.split(".")
    obj = NS.get(parts[0])
    if obj is None:
        obj = __import__(parts[0])
    for p in parts[1:]:
        obj = getattr(obj, p)
    members = [m for m in dir(obj) if not m.startswith("__")]
    if filter:
        f = filter.lower()
        members = [m for m in members if f in m.lower()]
    out = {"name": name, "type": type(obj).__name__,
           "doc": (getattr(obj, "__doc__", None) or "")[:3000],
           "count": len(members)}
    if docs:
        rows = []
        for m in members[:limit]:
            try:
                d = (getattr(getattr(obj, m), "__doc__", None) or "").strip()
            except Exception:
                d = ""
            rows.append("%s: %s" % (m, d.split("\n")[0][:200]))
        out["members"] = rows
    else:
        out["members"] = members[:limit]
    return out


def widgets(filter=None, visible_only=True, limit=300):
    """List Qt widgets (class, objectName, geometry) to find panels/viewports."""
    QtGui = _mod("QtGui")
    app = QtGui.QApplication.instance()
    rows = []
    for w in app.allWidgets():
        try:
            if visible_only and not w.isVisible():
                continue
            cls = w.metaObject().className()
            on = w.objectName()
            title = w.windowTitle() if w.isWindow() else ""
            key = ("%s %s %s" % (cls, on, title)).lower()
            if filter and filter.lower() not in key:
                continue
            g = w.geometry()
            rows.append({"class": cls, "name": on, "title": title,
                         "w": g.width(), "h": g.height(), "window": w.isWindow()})
        except Exception:
            continue
    rows.sort(key=lambda r: -(r["w"] * r["h"]))
    return rows[:limit]


# ------------------------------------------------------------- session
def session_info():
    sfmApp = _mod("sfmApp")
    sfm = _mod("sfm")
    out = {"has_document": bool(_try(lambda: sfmApp.HasDocument(), False))}
    if not out["has_document"]:
        return out
    movie = _try(lambda: sfmApp.GetMovie(), None)
    out["movie"] = _try(lambda: _s(movie.GetName()))
    out["fps"] = _try(lambda: sfmApp.GetFramesPerSecond())
    out["head_time"] = _try(lambda: sfmApp.GetHeadTimeInSeconds())
    out["current_shot"] = _try(lambda: _s(sfm.GetCurrentShot().GetName()))
    out["current_animset"] = _try(lambda: _s(sfm.GetCurrentAnimationSet().GetName()))
    shots = []
    for s in (_try(lambda: list(sfmApp.GetShots()), []) or []):
        e = {"name": _try(lambda: _s(s.GetName()))}
        e["map"] = _try(lambda: _s(s.mapname))
        e["start"] = _try(lambda: s.GetStartTime().GetSeconds())
        e["duration"] = _try(lambda: s.GetDuration().GetSeconds())
        e["camera"] = _try(lambda: _s(s.camera.GetName()))
        e["animation_sets"] = _try(lambda: [_s(a.GetName()) for a in s.animationSets], [])
        shots.append(e)
    out["shots"] = shots
    return out


def console(cmd):
    """Run an SFM/engine console command (e.g. 'map ctf_2fort')."""
    sfmApp = _mod("sfmApp")
    return _try(lambda: sfmApp.ExecuteGameCommand(str(cmd)))


# ------------------------------------------------------------ scene API
# Lives in sfm_mcp_scene.py; reloaded together with this module.
import sfm_mcp_scene
sfm_mcp_scene = reload(sfm_mcp_scene)
S = sfm_mcp_scene
call = S.call
