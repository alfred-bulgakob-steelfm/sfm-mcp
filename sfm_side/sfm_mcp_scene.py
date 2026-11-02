# -*- coding: utf-8 -*-
"""sfm-mcp scene API (Python 2.7, inside SFM). Exposed as H.S, called via H.call.

Works on the DME layer (vs + sfmApp), which needs no SFM script context.
Frames are relative to the start of the shot (0 = first frame), like
sfm.SetCurrentFrame. Rotations are Source QAngle degrees [pitch, yaw, roll].
Positions are SFM/engine units (inches), Z up; cameras look along +X.
All edits must run inside the bridge's undo scope (sfm_exec does that).
"""
import json
import math
import sys


def _m(name):
    return sys.modules[name]


def _s(v):
    try:
        return str(v)
    except Exception:
        return repr(v)


def _try(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default if default is not None else "<error: %s>" % str(e)[:200]


def _bytes(v):
    if isinstance(v, unicode):
        return v.encode("utf-8")
    if isinstance(v, list):
        return [_bytes(x) for x in v]
    if isinstance(v, dict):
        return dict((_bytes(k), _bytes(x)) for k, x in v.items())
    return v


def call(fn, json_args):
    """Entry point for the MCP server: H.call('name', '{json kwargs}')."""
    kwargs = json.loads(json_args) if json_args else {}
    # SWIG wants byte strings (char const *), JSON gives unicode.
    kwargs = dict((str(k), _bytes(v)) for k, v in kwargs.items())
    if fn.startswith("_") or fn not in PUBLIC:
        raise ValueError("unknown scene function %r" % fn)
    return globals()[fn](**kwargs)


# ------------------------------------------------------------ time/shots
def _fps():
    return float(_m("sfmApp").GetFramesPerSecond())


def _unwrap(x):
    """Generic (non-typed) elements hand out CDmAttribute wrappers."""
    if type(x).__name__ == "CDmAttribute":
        return x.GetValue()
    return x


def _secs(t):
    t = _unwrap(t)
    if hasattr(t, "GetSeconds"):
        return float(t.GetSeconds())
    return float(t)


def _num(x):
    return float(_unwrap(x))


def _shot(name=None):
    sfmApp = _m("sfmApp")
    shots = list(sfmApp.GetShots())
    if not shots:
        raise ValueError("session has no shots")
    if name:
        for s in shots:
            if _s(s.GetName()) == name:
                return s
        raise ValueError("no shot named %r; shots: %s" % (name, [_s(s.GetName()) for s in shots]))
    cur = _try(lambda: sfmApp.GetShotAtCurrentTime(), 0)
    return cur if cur not in (0, None) and not isinstance(cur, str) else shots[0]


def _animset(name, shot):
    names = []
    for a in shot.animationSets:
        n = _s(a.GetName())
        names.append(n)
        if n == name:
            return a
    raise ValueError("no animation set %r in shot %s; have: %s" % (name, _s(shot.GetName()), names))


def _channels_clip(shot, aset):
    tg = shot.FindTrackGroup("channelTrackGroup")
    if tg is None:
        return None
    for tr in tg.tracks:
        for c in tr.children:
            if _s(c.GetName()) == _s(aset.GetName()):
                return c
    return None


def _to_child(clip, t):
    try:
        return clip.ToChildMediaTime(t, False)  # no clamping to the clip range
    except TypeError:
        return clip.ToChildMediaTime(t)


def _time(shot, aset, frame):
    """Shot-relative frame -> DmeTime_t in the animation set's log time,
    using SFM's own clip mapping (movie -> shot media -> channels clip)."""
    vs = _m("vs")
    movie_t = vs.DmeTime_t(_secs(shot.timeFrame.start) + float(frame) / _fps())
    t = _to_child(shot, movie_t)
    cc = _channels_clip(shot, aset) if aset is not None else None
    if cc is not None:
        t = _to_child(cc, t)
    return t


def head_frame(shot=None):
    """Current playhead as a frame relative to the shot start."""
    sh = shot if (shot is not None and not isinstance(shot, basestring)) else _shot(shot)
    s = _m("sfmApp").GetHeadTimeInSeconds() - _secs(sh.timeFrame.start)
    return int(round(s * _fps()))


# --------------------------------------------------------- rotation math
def euler_to_quat(pitch, yaw, roll):
    p, y, r = [math.radians(float(a)) * 0.5 for a in (pitch, yaw, roll)]
    sp, cp = math.sin(p), math.cos(p)
    sy, cy = math.sin(y), math.cos(y)
    sr, cr = math.sin(r), math.cos(r)
    return _m("vs").Quaternion(sr * cp * cy - cr * sp * sy,
                               cr * sp * cy + sr * cp * sy,
                               cr * cp * sy - sr * sp * cy,
                               cr * cp * cy + sr * sp * sy)


def quat_to_euler(q):
    x, y, z, w = float(q.x), float(q.y), float(q.z), float(q.w)
    f0, f1, f2 = 1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y)
    l0, l1, l2 = 2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)
    u2 = 1 - 2 * (x * x + y * y)
    xy = math.sqrt(f0 * f0 + f1 * f1)
    if xy > 0.001:
        yaw, pitch, roll = math.atan2(f1, f0), math.atan2(-f2, xy), math.atan2(l2, u2)
    else:
        yaw, pitch, roll = math.atan2(-l0, l1), math.atan2(-f2, xy), 0.0
    return [round(math.degrees(a), 3) for a in (pitch, yaw, roll)]


def _vec(v):
    return [round(float(v.x), 3), round(float(v.y), 3), round(float(v.z), 3)]


def look_at_angles(src, dst):
    dx, dy, dz = [float(dst[i]) - float(src[i]) for i in range(3)]
    return [math.degrees(math.atan2(-dz, math.sqrt(dx * dx + dy * dy))),
            math.degrees(math.atan2(dy, dx)), 0.0]


# ------------------------------------------------------------ inspection
def _kind(a):
    if _try(lambda: a.HasAttribute("gameModel") and a.gameModel is not None, False):
        return "model"
    if _try(lambda: a.HasAttribute("camera") and a.camera is not None, False):
        return "camera"
    names = set(_s(c.GetName()) for c in a.controls)
    if "intensity" in names or "color" in names:
        return "light"
    return "other"


def animsets(shot=None):
    """Animation sets of a shot with kind, model path and root transform."""
    sh = _shot(shot)
    fr = head_frame(sh)
    out = []
    for a in sh.animationSets:
        e = {"name": _s(a.GetName()), "kind": _kind(a), "controls": len(a.controls)}
        if e["kind"] == "model":
            e["model"] = _try(lambda: _s(a.gameModel.GetModelName()))
        if a.FindControl("rootTransform") is not None:
            e["root"] = _try(lambda: get_transform(e["name"], frame=fr, shot=shot))
        out.append(e)
    return {"shot": _s(sh.GetName()), "frame": fr, "fps": _fps(), "animsets": out}


def controls(animset, filter=None, shot=None):
    """List controls of an animation set (bones = [transform], sliders = values)."""
    a = _animset(animset, _shot(shot))
    out = []
    for c in a.controls:
        n = _s(c.GetName())
        if filter and filter.lower() not in n.lower():
            continue
        if _s(c.GetTypeString()) == "DmeTransformControl":
            out.append(n + " [transform]")
        elif c.HasAttribute("rightValue"):
            out.append("%s [stereo L=%.3f R=%.3f]" % (n, _num(c.GetValue("leftValue")),
                                                       _num(c.GetValue("rightValue"))))
        elif c.HasAttribute("value"):
            out.append("%s [%.3f]" % (n, _num(c.GetValue("value"))))
        else:
            out.append("%s [%s]" % (n, _s(c.GetTypeString())))
    return {"animset": animset, "count": len(out), "controls": out}


def _tcontrol(a, control):
    c = a.FindControl(control)
    if c is None or not c.HasAttribute("positionChannel"):
        raise ValueError("no transform control %r in %s" % (control, _s(a.GetName())))
    return c


def get_transform(animset, control="rootTransform", frame=None, shot=None):
    sh = _shot(shot)
    a = _animset(animset, sh)
    c = _tcontrol(a, control)
    fr = head_frame(sh) if frame is None else int(frame)
    t = _time(sh, a, fr)
    return {"control": control, "frame": fr,
            "position": _vec(c.positionChannel.log.GetValue(t)),
            "rotation": quat_to_euler(c.orientationChannel.log.GetValue(t)),
            "pos_keys": c.positionChannel.log.GetKeyCount(),
            "rot_keys": c.orientationChannel.log.GetKeyCount()}


def _channels_of(c):
    if c.HasAttribute("positionChannel"):
        return [("position", c.positionChannel), ("rotation", c.orientationChannel)]
    out = []
    for an in ("channel", "leftvaluechannel", "rightvaluechannel"):
        if c.HasAttribute(an) and c.GetValue(an) is not None:
            out.append((an, c.GetValue(an)))
    return out


def keys(animset, control="rootTransform", shot=None):
    """Keyframe frame numbers of a control's channels."""
    sh = _shot(shot)
    a = _animset(animset, sh)
    c = a.FindControl(control)
    if c is None:
        raise ValueError("no control %r in %s" % (control, animset))
    fps, t0 = _fps(), _secs(_time(sh, a, 0))
    return dict((label, _ranges([int(round((_secs(ch.log.GetKeyTime(i)) - t0) * fps))
                                 for i in range(ch.log.GetKeyCount())]))
                for label, ch in _channels_of(c))


def _ranges(frames):
    """[0,1,2,3,7,9,10,11] -> '0-3 7 9-11' (baked animation = a key per frame)."""
    out, i = [], 0
    while i < len(frames):
        j = i
        while j + 1 < len(frames) and frames[j + 1] == frames[j] + 1:
            j += 1
        out.append(str(frames[i]) if j - i < 2 else "%d-%d" % (frames[i], frames[j]))
        if 0 < j - i < 2:
            out.append(str(frames[j]))
        i = j + 1
    return " ".join(out)


# --------------------------------------------------------------- editing
def _set_key(log, t, value, static):
    if static and log.GetKeyCount() == 1:
        log.SetKey(log.GetKeyTime(0), value)
        return "static"
    # InsertKey keeps later keys; SetKey deletes every key after t.
    log.InsertKey(t, value)
    return "key"


def shift(animset, offset, control="rootTransform", shot=None):
    """Move a control's whole position animation by offset [dx,dy,dz]
    (every key), e.g. to put a floating character on the floor or to move
    a baked walk elsewhere."""
    sh = _shot(shot)
    a = _animset(animset, sh)
    c = a.FindControl(control)
    if c is None or not c.HasAttribute("positionChannel"):
        raise ValueError("no transform control %r in %s" % (control, animset))
    vs = _m("vs")
    lg = c.positionChannel.log
    d = [float(x) for x in offset]
    times = [lg.GetKeyTime(i) for i in range(lg.GetKeyCount())]
    vals = [_copy(lg.GetValue(t)) for t in times]
    for t, v in zip(times, vals):
        lg.InsertKey(t, vs.Vector(v.x + d[0], v.y + d[1], v.z + d[2]))
    p = _copy(c.GetValue("valuePosition"))
    c.SetValue("valuePosition", vs.Vector(p.x + d[0], p.y + d[1], p.z + d[2]))
    return {"animset": animset, "control": control, "keys_moved": len(times)}


def set_transform(animset, position=None, rotation=None, control="rootTransform",
                  frame=None, relative=False, shot=None):
    """position [x,y,z] / rotation [pitch,yaw,roll] of a transform control.
    frame=None: static pose if the control is not animated (<=1 key), else a
    key at the playhead. frame=N: key at shot frame N."""
    vs = _m("vs")
    sh = _shot(shot)
    a = _animset(animset, sh)
    c = _tcontrol(a, control)
    pch, och = c.positionChannel, c.orientationChannel
    static = frame is None and pch.log.GetKeyCount() <= 1 and och.log.GetKeyCount() <= 1
    fr = head_frame(sh) if frame is None else int(frame)
    t = _time(sh, a, fr)
    done = {}
    if position is not None:
        p = [float(x) for x in position]
        if relative:
            cur = pch.log.GetValue(t)
            p = [cur.x + p[0], cur.y + p[1], cur.z + p[2]]
        v = vs.Vector(p[0], p[1], p[2])
        done["position"] = _set_key(pch.log, t, v, static)
        c.valuePosition = v
        if pch.toElement is not None:
            pch.toElement.position = v
    if rotation is not None:
        r = [float(x) for x in rotation]
        if relative:
            cur = quat_to_euler(och.log.GetValue(t))
            r = [cur[0] + r[0], cur[1] + r[1], cur[2] + r[2]]
        q = euler_to_quat(*r)
        done["rotation"] = _set_key(och.log, t, q, static)
        c.valueOrientation = q
        if och.toElement is not None:
            och.toElement.orientation = q
    res = get_transform(animset, control, fr, shot)
    res["mode"] = done
    return res


def set_control(animset, control, value=None, left=None, right=None, frame=None, shot=None):
    """Slider control (face flex etc., usually 0..1). Stereo controls take
    value (both sides) or left/right."""
    sh = _shot(shot)
    a = _animset(animset, sh)
    c = a.FindControl(control)
    if c is None:
        raise ValueError("no control %r in %s" % (control, animset))
    fr = head_frame(sh) if frame is None else int(frame)
    t = _time(sh, a, fr)
    if c.HasAttribute("rightValue"):
        plan = [("leftValue", "leftvaluechannel", left if left is not None else value),
                ("rightValue", "rightvaluechannel", right if right is not None else value)]
    else:
        if value is None:
            raise ValueError("value is required for a mono control")
        plan = [("value", "channel", value)]
    done = {}
    for attr, chan, v in plan:
        if v is None:
            continue
        ch = c.GetValue(chan)
        done[attr] = _set_key(ch.log, t, float(v), frame is None and ch.log.GetKeyCount() <= 1)
        c.SetValue(attr, float(v))
    return {"control": control, "frame": fr, "set": done}


# ---------------------------------------------------------------- camera
def _same(a, b):
    """DME wrappers are new Python objects on every access: compare handles."""
    return a is not None and b is not None and a.GetHandle() == b.GetHandle()


def _camera_set(sh, cam):
    for a in sh.animationSets:
        if a.HasAttribute("camera") and _same(a.GetValue("camera"), cam):
            return a
    return None


def camera(animset=None, position=None, rotation=None, look_at=None, fov=None,
           exposure=None, focal_distance=None, active=None, name=None, shot=None):
    """Get or set a camera. animset=None: the shot's active camera.
    look_at=[x,y,z] aims it; fov degrees (10-120); exposure = toneMapScale
    (1 = default, lower = darker); active=True makes it the shot camera.
    Cameras with an animation set are changed through its controls
    (static, like moving it in the viewport)."""
    sh = _shot(shot)
    if animset:
        a = _animset(animset, sh)
        if not a.HasAttribute("camera") or a.GetValue("camera") is None:
            raise ValueError("%s is not a camera animation set" % animset)
        cam = a.GetValue("camera")
    else:
        cam = sh.camera
        if cam is None:
            raise ValueError("shot has no camera (add a model or camera first)")
        a = _camera_set(sh, cam)
    if active:
        sh.SetValue("camera", cam)
    if name:
        if a is not None:
            a.SetName(_s(name))
        cam.SetName(_s(name))
    if a is not None and a.FindControl("transform") is not None:
        an = _s(a.GetName())
        if position is not None or rotation is not None or look_at is not None:
            if look_at is not None:
                src = position if position is not None else _vec(cam.transform.position)
                rotation = look_at_angles(src, look_at)
            set_transform(an, position=position, rotation=rotation, control="transform",
                          shot=_s(sh.GetName()))
        for ctl, real in (("fieldOfView", fov), ("toneMapScale", exposure),
                          ("focalDistance", focal_distance)):
            if real is not None and a.FindControl(ctl) is not None:
                _slider(a, ctl, real=real, shot=_s(sh.GetName()))
        tr = get_transform(an, "transform", shot=_s(sh.GetName()))
        pos, rot = tr["position"], tr["rotation"]
        vals = dict((k, _slider_real(a, c)) for k, c in (("fov", "fieldOfView"),
                    ("exposure", "toneMapScale"), ("focal_distance", "focalDistance"))
                    if a.FindControl(c) is not None)
    else:
        tr = cam.transform
        if position is not None:
            tr.position = _m("vs").Vector(*[float(x) for x in position])
        if look_at is not None:
            rotation = look_at_angles(_vec(tr.position), look_at)
        if rotation is not None:
            tr.orientation = euler_to_quat(*rotation)
        if fov is not None:
            cam.SetValue("fieldOfView", float(fov))
        if exposure is not None:
            cam.SetValue("toneMapScale", float(exposure))
        pos, rot = _vec(tr.position), quat_to_euler(tr.orientation)
        vals = {"fov": _num(cam.GetValue("fieldOfView")),
                "exposure": _try(lambda: _num(cam.GetValue("toneMapScale")))}
    out = {"camera": _s(cam.GetName()), "animset": _s(a.GetName()) if a is not None else None,
           "active": _same(sh.camera, cam), "position": pos, "rotation": rot}
    out.update(vals)
    return out


# ------------------------------------------------------------- animation
# Sequences are sampled with the engine's own decoder
# (vs.Studio_GetSequenceBonesAtFrame -> model-space bone matrices), turned
# into parent-space transforms and written as keys on the bone controls.
# Blend sequences (TF2 run_MELEE = 3x3 grid of a_run*_MELEE) evaluate at
# their center pose, so a single animation of the grid is played by
# pointing every grid cell at it for the duration of the sampling.
STUDIO_LOOPING = 0x0001


def _model(a):
    gm = _try(lambda: a.gameModel, None)
    if gm is None or isinstance(gm, basestring):
        raise ValueError("%s is not a model animation set" % _s(a.GetName()))
    return gm, gm.GetModelHandle(), gm.GetStudioHdr()


def _blend_table(sd):
    import ctypes
    n = sd.GetGroupSize(0) * sd.GetGroupSize(1)
    return (ctypes.c_short * n).from_address(int(sd.this) + sd.animindexindex)


def _anim_name(hdr, si, raw):
    return _s(hdr.pAnimdesc(hdr.iRelativeAnim(si, raw)).pszName())


def sequences(animset, filter=None, shot=None, limit=400):
    """Animations stored in the model. Blend sequences list the single
    animations inside them (shown when a filter is given); apply_animation
    accepts both kinds of names."""
    vs = _m("vs")
    a = _animset(animset, _shot(shot))
    gm, h, hdr = _model(a)
    f = filter.lower() if filter else None
    out = []
    for name, i in _seq_index(h):
        sd = hdr.pSeqdesc(i)
        parts = [_anim_name(hdr, i, r) for r in _blend_table(sd)] if sd.numblends > 1 else []
        if f and f not in name.lower() and not any(f in p.lower() for p in parts):
            continue
        e = {"name": name, "frames": vs.Studio_CalcSequenceFrameCount(h, i),
             "loop": bool(sd.flags & STUDIO_LOOPING)}
        if parts:
            e["blend"] = parts if f else len(parts)
        out.append(e)
        if len(out) >= limit:
            break
    return {"model": _s(gm.GetModelName()), "count": len(out), "sequences": out}


def _seq_index(h):
    """[(name, sequence index)]. Studio_GetSequenceList is a filtered list
    (210 of 319 for the TF2 heavy): its positions are NOT sequence indices."""
    vs = _m("vs")
    return [(_s(n), vs.Studio_FindSequenceIndex(h, _s(n))) for n in vs.Studio_GetSequenceList(h)]


def _resolve_anim(h, hdr, name):
    seqs = _seq_index(h)
    low = name.lower()
    for n, i in seqs:
        if n.lower() == low:
            return i, None, n
    for _, i in seqs:
        sd = hdr.pSeqdesc(i)
        if sd.numblends > 1:
            for r in _blend_table(sd):
                n = _anim_name(hdr, i, r)
                if n.lower() == low:
                    return i, int(r), n
    raise ValueError("no sequence or animation %r in this model (see sequences)" % name)


def _sample(h, hdr, si, raw, parents):
    """-> (frames, fps, loop, step): frames = per unique frame a list of
    (Vector pos, Quaternion rot) in parent space; step = root motion per
    animation frame in model space."""
    vs = _m("vs")
    ml = vs.mathlib
    sd = hdr.pSeqdesc(si)
    tbl = _blend_table(sd)
    saved = list(tbl)
    try:
        if raw is not None:
            for k in range(len(tbl)):
                tbl[k] = raw
        ad = hdr.pAnimdesc(hdr.iRelativeAnim(si, sd.anim(0, 0)))
        fps = float(ad.fps) or 30.0
        step = [0.0, 0.0, 0.0]
        if ad.nummovements > 0:
            mv = ad.pMovement(ad.nummovements - 1)
            if mv.endframe > 0:
                step = [mv.position.x / mv.endframe, mv.position.y / mv.endframe,
                        mv.position.z / mv.endframe]
        count = max(1, vs.Studio_CalcSequenceFrameCount(h, si))
        mats = [vs.Studio_GetSequenceBonesAtFrame(h, si, f) for f in range(count)]
    finally:
        for k in range(len(tbl)):
            tbl[k] = saved[k]
    frames = []
    for ms in mats:
        row = []
        for i, m in enumerate(ms):
            p = parents[i]
            if p >= 0:
                inv = ml.matrix3x4_t()
                ml.MatrixInvert(ms[p], inv)
                loc = ml.matrix3x4_t()
                ml.ConcatTransforms(inv, m, loc)
            else:
                loc = m
            pos, q = vs.Vector(), vs.Quaternion()
            ml.MatrixPosition(loc, pos)
            ml.MatrixQuaternion(loc, q)
            row.append((pos, q))
        frames.append(row)
    return frames, fps, bool(sd.flags & STUDIO_LOOPING), step


def _qrot(q, v):
    """Rotate vector v [x,y,z] by quaternion q."""
    x, y, z, w = q.x, q.y, q.z, q.w
    tx = 2 * (y * v[2] - z * v[1])
    ty = 2 * (z * v[0] - x * v[2])
    tz = 2 * (x * v[1] - y * v[0])
    return [v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx)]


def _copy(v):
    """log.GetValue() returns a reference to a buffer that the next GetValue
    overwrites: copy before keeping it."""
    vs = _m("vs")
    n = type(v).__name__
    if n == "Vector":
        return vs.Vector(v.x, v.y, v.z)
    if n == "Quaternion":
        return vs.Quaternion(v.x, v.y, v.z, v.w)
    return v


def _rekey(log, ts, values, t_in=None, t_out=None):
    """Replace the keys from t_in (or ts[0]) to t_out (or ts[-1]) by values
    at ts. If the log has keys before t_in / after t_out, a key with the old
    value is kept there so that animation blends in/out; otherwise the
    first/last new frame simply holds."""
    n = log.GetKeyCount()
    first = _secs(log.GetKeyTime(0)) if n else None
    last = _secs(log.GetKeyTime(n - 1)) if n else None
    hold = []
    if t_in is not None and n and first < _secs(t_in) - 1e-4:
        hold.append((t_in, _copy(log.GetValue(t_in))))
    if t_out is not None and n and last > _secs(t_out) + 1e-4:
        hold.append((t_out, _copy(log.GetValue(t_out))))
    log.RemoveKeys(t_in if t_in is not None else ts[0], t_out if t_out is not None else ts[-1])
    for t, v in hold:
        log.InsertKey(t, v)
    for t, v in zip(ts, values):
        log.InsertKey(t, v)


def apply_animation(animset, animation, start_frame=0, end_frame=None, loops=None,
                    rate=1.0, root_motion=True, blend=2, shot=None):
    """Bake a model animation (sequence, or one animation of a blend like
    a_runN_MELEE) onto the bone controls from start_frame. Length: end_frame,
    else `loops` passes (default 1). Looping animations repeat. root_motion
    moves rootTransform along the model's facing by the animation's own
    ground speed (no foot sliding). blend: frames to blend in/out from the
    previous pose."""
    vs = _m("vs")
    ml = vs.mathlib
    sh = _shot(shot)
    a = _animset(animset, sh)
    gm, h, hdr = _model(a)
    si, raw, label = _resolve_anim(h, hdr, animation)
    nb = hdr.numbones
    bnames = [_s(hdr.pBone(i).pszName()) for i in range(nb)]
    parents = [hdr.pBone(i).parent for i in range(nb)]
    frames, fps, loop, step = _sample(h, hdr, si, raw, parents)
    count = len(frames)
    sfps, rate = _fps(), float(rate)
    start = int(start_frame)
    if end_frame is None:
        span = (count if loop else max(count - 1, 1)) * float(loops or 1)
        end_frame = start + int(math.ceil(span / fps * sfps / rate))
    end = max(int(end_frame), start + 1)
    shot_frames = range(start, end + 1)
    ts = [_time(sh, a, k) for k in shot_frames]
    blend = max(0, int(blend))
    t_in = _time(sh, a, start - blend) if blend else None
    t_out = _time(sh, a, end + blend) if blend else None

    def anim_pos(k):
        af = (k - start) / sfps * fps * rate
        if loop:
            af %= count
            i0 = int(af)
            return i0, (i0 + 1) % count, af - i0
        af = min(af, count - 1)
        i0 = int(af)
        return i0, min(i0 + 1, count - 1), af - i0

    plan = [anim_pos(k) for k in shot_frames]
    keyed, missing = 0, []
    for b, bn in enumerate(bnames):
        c = a.FindControl(bn)
        if c is None or not c.HasAttribute("positionChannel"):
            missing.append(bn)
            continue
        pv, qv = [], []
        for i0, i1, w in plan:
            p0, q0 = frames[i0][b]
            p1, q1 = frames[i1][b]
            pv.append(vs.Vector(p0.x + (p1.x - p0.x) * w, p0.y + (p1.y - p0.y) * w,
                                p0.z + (p1.z - p0.z) * w))
            q = vs.Quaternion()
            ml.QuaternionSlerp(q0, q1, w, q)
            qv.append(q)
        _rekey(c.positionChannel.log, ts, pv, t_in, t_out)
        _rekey(c.orientationChannel.log, ts, qv, t_in, t_out)
        keyed += 1

    res = {"animation": label, "sequence": _s(hdr.pSeqdesc(si).pszLabel()),
           "frames": [start, end], "anim_frames": count, "anim_fps": fps, "loop": loop,
           "rate": rate, "bones_keyed": keyed,
           "bones_without_control": len(missing)}  # helper/procedural bones
    if raw is None and hdr.pSeqdesc(si).numblends > 1:
        res["note"] = ("blend sequence evaluated at its center pose; pass one of its "
                       "animations instead: %s" % [_anim_name(hdr, si, r)
                                                  for r in _blend_table(hdr.pSeqdesc(si))])
    speed = math.sqrt(sum(x * x for x in step)) * fps * rate
    res["ground_speed"] = round(speed, 2)
    rt = a.FindControl("rootTransform")
    if root_motion and speed > 0.01 and rt is not None:
        L = rt.positionChannel.log
        p0 = _copy(L.GetValue(ts[0]))
        q0 = _copy(rt.orientationChannel.log.GetValue(ts[0]))
        n_af = (end - start) / sfps * fps * rate
        d = _qrot(q0, [s * n_af for s in step])
        p1 = vs.Vector(p0.x + d[0], p0.y + d[1], p0.z + d[2])
        _rekey(L, [ts[0], ts[-1]], [vs.Vector(p0.x, p0.y, p0.z), p1])
        later = [int(round((_secs(L.GetKeyTime(i)) - _secs(ts[0])) * sfps)) + start
                 for i in range(L.GetKeyCount()) if _secs(L.GetKeyTime(i)) > _secs(ts[-1]) + 1e-4]
        res["root_motion"] = {"from": _vec(p0), "to": _vec(p1)}
        if later:
            res["root_motion"]["later_position_keys"] = later
    return res


# ------------------------------------------------------------ hand posing
# Own keyframe animation, no stored animations. Rotations are given in
# CHARACTER space (model space under rootTransform): X forward, Y left,
# Z up. Quaternions here are plain (x, y, z, w) tuples, Hamilton product,
# world = parent * local (same as Source's ConcatTransforms).
def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def _qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _qvec(q, v):
    p = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), _qconj(q))
    return [p[0], p[1], p[2]]


def _qaxis(axis, deg):
    h = math.radians(deg) / 2.0
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _qarc(u, v):
    """Shortest rotation taking unit vector u to unit vector v."""
    d = u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    if d < -0.99999:
        ax = [0.0, -u[2], u[1]] if abs(u[0]) < 0.9 else [-u[2], 0.0, u[0]]
        return _qaxis(_norm(ax), 180.0)
    c = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    q = (c[0], c[1], c[2], 1.0 + d)
    n = math.sqrt(sum(x * x for x in q))
    return tuple(x / n for x in q)


def _rig(a):
    """Skeleton of a model animset: names, parents, controls, ref pose."""
    vs = _m("vs")
    gm, h, hdr = _model(a)
    nb = hdr.numbones
    names = [_s(hdr.pBone(i).pszName()) for i in range(nb)]
    parents = [hdr.pBone(i).parent for i in range(nb)]
    ref = _sample(h, hdr, vs.Studio_FindSequenceIndex(h, "ref"), None, parents)[0][0]
    ctrls = []
    for n in names:
        c = a.FindControl(n)
        ctrls.append(c if c is not None and c.HasAttribute("positionChannel") else None)
    kids = [[] for _ in names]
    for i, p in enumerate(parents):
        if p >= 0:
            kids[p].append(i)
    return {"names": names, "parents": parents, "ctrls": ctrls, "kids": kids,
            "ref": [((p.x, p.y, p.z), (q.x, q.y, q.z, q.w)) for p, q in ref]}


def _local_pose(rig, t):
    out = []
    for i, c in enumerate(rig["ctrls"]):
        if c is None:
            out.append(rig["ref"][i])
            continue
        p = c.positionChannel.log.GetValue(t)
        p = (p.x, p.y, p.z)   # read now: GetValue reuses its buffer
        q = c.orientationChannel.log.GetValue(t)
        out.append((p, (q.x, q.y, q.z, q.w)))
    return out


def _fk(rig, loc):
    """Character-space (positions, quaternions) of every bone."""
    wp, wq = [], []
    for i, p in enumerate(rig["parents"]):
        lp, lq = loc[i]
        if p < 0:
            wp.append(list(lp))
            wq.append(lq)
        else:
            r = _qvec(wq[p], lp)
            wp.append([wp[p][0] + r[0], wp[p][1] + r[1], wp[p][2] + r[2]])
            wq.append(_qmul(wq[p], lq))
    return wp, wq


def _end_child(rig, i):
    """The joint a bone points at: its bip_* child farthest away."""
    kids = rig["kids"][i]
    if not kids:
        return None
    main = [k for k in kids if rig["names"][k].startswith("bip_")] or kids
    return max(main, key=lambda k: sum(x * x for x in rig["ref"][k][0]))


def _bone(rig, name):
    low = name.lower()
    for i, n in enumerate(rig["names"]):
        if n.lower() == low:
            return i
    raise ValueError("no bone %r (bones: sfm_list_controls)" % name)


def _pattern_bones(rig, names):
    """Bone names; '*' wildcards allowed (e.g. 'bip_*_R' fingers...)."""
    import fnmatch
    out = []
    for n in names:
        if "*" in n or "?" in n:
            hits = [i for i, b in enumerate(rig["names"])
                    if fnmatch.fnmatch(b.lower(), n.lower()) and rig["ctrls"][i] is not None]
            if not hits:
                raise ValueError("no bone matches %r" % n)
            out += hits
        else:
            out.append(_bone(rig, n))
    return out


def _apply_pose(rig, loc, spec):
    """Change local pose `loc` in place from a pose spec; -> touched bones.
    spec keys: reset [bones] (back to the model's reference pose, e.g. an
    open hand), move {bone: [dx,dy,dz]}, rotate {bone: [rx,ry,rz]},
    aim {bone: [x,y,z] | "parent"}, twist {bone: deg}. Applied per bone,
    parents first, in that order; all in character axes."""
    def by_index(d):
        out = {}
        for k, v in (d or {}).items():
            for i in _pattern_bones(rig, [k]):
                out[i] = v
        return out
    reset = set(_pattern_bones(rig, spec.get("reset") or []))
    move, rotate = by_index(spec.get("move")), by_index(spec.get("rotate"))
    aim, twist = by_index(spec.get("aim")), by_index(spec.get("twist"))
    touched = sorted(reset | set(move) | set(rotate) | set(aim) | set(twist))
    for i in touched:                         # studio order: parents first
        if rig["ctrls"][i] is None:
            raise ValueError("bone %s has no control" % rig["names"][i])
        p = rig["parents"][i]
        wp, wq = _fk(rig, loc)
        pq = wq[p] if p >= 0 else (0.0, 0.0, 0.0, 1.0)
        lp, lq = loc[i]
        if i in reset:
            lq = rig["ref"][i][1]
        if i in move:
            d = _qvec(_qconj(pq), [float(x) for x in move[i]])
            lp = (lp[0] + d[0], lp[1] + d[1], lp[2] + d[2])
        w = _qmul(pq, lq)
        if i in rotate:
            rx, ry, rz = [float(x) for x in rotate[i]]
            dq = _qmul(_qaxis((0, 0, 1), rz), _qmul(_qaxis((0, 1, 0), ry), _qaxis((1, 0, 0), rx)))
            w = _qmul(dq, w)
        c = _end_child(rig, i)
        if i in aim:
            if c is None:
                raise ValueError("bone %s has no child joint to aim" % rig["names"][i])
            target = aim[i]
            if isinstance(target, basestring):   # "parent": straighten the chain
                target = [wp[i][k] - wp[p][k] for k in range(3)]
            cur = _norm(_qvec(w, loc[c][0]))
            w = _qmul(_qarc(cur, _norm([float(x) for x in target])), w)
        if i in twist:
            if c is None:
                raise ValueError("bone %s has no child joint to twist around" % rig["names"][i])
            w = _qmul(_qaxis(_norm(_qvec(w, loc[c][0])), float(twist[i])), w)
        loc[i] = (lp, _qmul(_qconj(pq), w))
    return touched


def _ends(rig, loc, bones):
    wp, _ = _fk(rig, loc)
    out = {}
    for i in bones:
        c = _end_child(rig, i)
        out[rig["names"][i]] = [round(x, 1) for x in wp[c if c is not None else i]]
    return out


def pose(animset, frame=None, rotate=None, aim=None, move=None, twist=None, reset=None,
         report=None, shot=None):
    """Key a hand-made pose at ONE frame, absolute keys (see _apply_pose).
    For motion over time prefer animate(): it layers smoothly on top of
    the existing animation."""
    vs = _m("vs")
    sh = _shot(shot)
    a = _animset(animset, sh)
    rig = _rig(a)
    fr = head_frame(sh) if frame is None else int(frame)
    t = _time(sh, a, fr)
    loc = _local_pose(rig, t)
    base = list(loc)
    touched = _apply_pose(rig, loc, {"rotate": rotate, "aim": aim, "move": move,
                                     "twist": twist, "reset": reset})
    for i in touched:
        ctl = rig["ctrls"][i]
        if loc[i][0] != base[i][0]:
            ctl.positionChannel.log.InsertKey(t, vs.Vector(*loc[i][0]))
        ctl.orientationChannel.log.InsertKey(t, vs.Quaternion(*loc[i][1]))
    rep = touched[:12] + [_bone(rig, n) for n in (report or [])]
    return {"frame": fr, "keyed": len(touched), "ends": _ends(rig, loc, rep)}


def _slerp(a, b, u):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b, d = tuple(-x for x in b), -d
    if d > 0.9995:
        r = [x + (y - x) * u for x, y in zip(a, b)]
    else:
        th = math.acos(min(1.0, d))
        s = math.sin(th)
        wa, wb = math.sin((1 - u) * th) / s, math.sin(u * th) / s
        r = [wa * x + wb * y for x, y in zip(a, b)]
    n = math.sqrt(sum(x * x for x in r))
    return tuple(x / n for x in r)


_IDENT = ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))


def _layer_at(curve, f, ease):
    """curve [(frame, dq, dp, ease_or_None)] -> (dq, dp) at frame f."""
    for j in range(len(curve) - 1):
        fa, qa, pa, _ = curve[j]
        fb, qb, pb, eb = curve[j + 1]
        if fa <= f <= fb:
            u = float(f - fa) / (fb - fa) if fb > fa else 1.0
            if (eb or ease) == "smooth":
                u = u * u * (3 - 2 * u)
            return _slerp(qa, qb, u), tuple(x + (y - x) * u for x, y in zip(pa, pb))
    last = curve[-1] if f > curve[-1][0] else curve[0]
    return last[1], last[2]


def animate(animset, keys, ease="smooth", report=None, shot=None):
    """Hand-made animation as an additive LAYER over the existing motion
    (idle breathing etc. keeps playing underneath).
    keys: [{"frame": f, <pose spec: rotate/aim/move/twist/reset>, "ease":
    "smooth"|"linear"}]. Each key's spec is evaluated against the
    underlying pose at that frame; the layer is identity at the first and
    last key frame unless a bone is posed there (make them rest keys:
    {"frame": f}). Between keys the offsets ease in/out (smooth) like an
    animator's pose-to-pose; a key per frame is baked over the range."""
    vs = _m("vs")
    sh = _shot(shot)
    a = _animset(animset, sh)
    rig = _rig(a)
    keys = sorted(keys, key=lambda k: int(k["frame"]))
    f0, f1 = int(keys[0]["frame"]), int(keys[-1]["frame"])
    if f1 <= f0:
        raise ValueError("need keys on at least two different frames")
    curves = {}
    for k in keys:
        fr = int(k["frame"])
        base = _local_pose(rig, _time(sh, a, fr))
        loc = list(base)
        for i in _apply_pose(rig, loc, k):
            dq = _qmul(_qconj(base[i][1]), loc[i][1])          # new = base * dq
            dp = tuple(n - b for n, b in zip(loc[i][0], base[i][0]))
            curves.setdefault(i, []).append((fr, dq, dp, k.get("ease")))
    for c in curves.values():
        if c[0][0] != f0:
            c.insert(0, (f0,) + _IDENT + (None,))
        if c[-1][0] != f1:
            c.append((f1,) + _IDENT + (None,))
    frames = range(f0, f1 + 1)
    ts = [_time(sh, a, f) for f in frames]
    for i, c in sorted(curves.items()):
        ctl = rig["ctrls"][i]
        pl, ql = ctl.positionChannel.log, ctl.orientationChannel.log
        moved = any(abs(x) > 1e-6 for _, _, dp, _ in c for x in dp)
        pv, qv = [], []
        for f, t in zip(frames, ts):
            dq, dp = _layer_at(c, f, ease)
            bq = ql.GetValue(t)
            qv.append(vs.Quaternion(*_qmul((bq.x, bq.y, bq.z, bq.w), dq)))
            if moved:
                bp = pl.GetValue(t)
                pv.append(vs.Vector(bp.x + dp[0], bp.y + dp[1], bp.z + dp[2]))
        _rekey(ql, ts, qv)
        if moved:
            _rekey(pl, ts, pv)
    out = {"frames": [f0, f1], "keys": len(keys), "bones": len(curves)}
    if report:
        rep = [_bone(rig, n) for n in report]
        out["ends"] = dict((k["frame"], _ends(rig, _local_pose(rig, _time(sh, a, int(k["frame"]))), rep))
                           for k in keys)
    return out


# ----------------------------------------------------------------- lights
_ADD_ACTIONS = {"light": "Create Animation Set for New Light",
                "camera": "Create Animation Set for New Camera"}


def add_element(kind):
    """Queue SFM's own Animation Set Editor 'Add' menu command (light or
    camera); it runs on the next event-loop pass (never synchronously:
    that crashed SFM)."""
    from PySide import QtCore, QtGui
    if kind not in _ADD_ACTIONS:
        raise ValueError("kind must be one of %s" % sorted(_ADD_ACTIONS))
    eds = [w for w in QtGui.QApplication.instance().allWidgets()
           if w.metaObject().className() == "CQAnimationSetEditor"]
    if not eds:
        raise ValueError("Animation Set Editor panel not found")
    btn = [b for b in eds[0].findChildren(QtGui.QToolButton) if b.toolTip() == "Add"]
    if not btn or btn[0].menu() is None:
        raise ValueError("Animation Set Editor 'Add' button not found")
    acts = [x for x in btn[0].menu().actions() if x.text() == _ADD_ACTIONS[kind]]
    if not acts:   # menu is filled lazily when shown
        QtCore.QMetaObject.invokeMethod(eds[0], "OnAboutToShowPlusMenu")
        acts = [x for x in btn[0].menu().actions() if x.text() == _ADD_ACTIONS[kind]]
    if not acts:
        raise ValueError("menu command %r not found" % _ADD_ACTIONS[kind])
    QtCore.QMetaObject.invokeMethod(acts[0], "trigger", QtCore.Qt.QueuedConnection)
    return {"queued": _ADD_ACTIONS[kind],
            "existing": [_s(a.GetName()) for a in _shot().animationSets]}


def _find_dag(root, name, type_part):
    if _s(root.GetName()) == name and type_part in root.GetTypeString():
        return root
    for ch in root.children:
        r = _find_dag(ch, name, type_part)
        if r is not None:
            return r
    return None


def _slider(a, name, real=None, norm=None, frame=None, shot=None):
    """Set a light/camera slider by its real value (inverts lerp(value,lo,hi))."""
    c = a.FindControl(name)
    if c is None:
        raise ValueError("no control %r" % name)
    if real is not None:
        op = c.GetValue("channel").toElement
        if op is not None and op.GetTypeString() == "DmeExpressionOperator":
            lo, hi = _num(op.GetValue("lo")), _num(op.GetValue("hi"))
            norm = (float(real) - lo) / (hi - lo) if hi != lo else 0.0
        else:
            norm = float(real)
    set_control(_s(a.GetName()), name, value=max(0.0, min(1.0, norm)), frame=frame, shot=shot)


def light(animset=None, name=None, position=None, rotation=None, look_at=None, color=None,
          intensity=None, fov=None, distance=None, softness=None, shadows=None,
          volumetric=None, volumetric_intensity=None, texture=None, frame=None, shot=None):
    """Get/set a projected light. animset=None: the last light in the shot.
    color [r,g,b] 0-255; intensity 0-1000 (default 500); fov degrees 10-120;
    distance = max reach in inches (60-3000); softness = shadow radius 0-50;
    shadows/volumetric bools; texture e.g. 'effects/flashlight001'.
    look_at [x,y,z] aims it (lights shine along +X like cameras).
    frame=N keys the change (animated light), else static. name renames."""
    sh = _shot(shot)
    sets = [a for a in sh.animationSets if a.FindControl("intensity") is not None
            and a.FindControl("horizontalFOV") is not None]
    if animset:
        sets = [a for a in sets if _s(a.GetName()) == animset]
    if not sets:
        raise ValueError("no light animation set %s" % (animset or "in this shot"))
    a = sets[-1]
    an = _s(a.GetName())
    L = _find_dag(sh.scene, an, "Light")
    if name:
        a.SetName(name)
        if L is not None:
            L.SetName(name)
        an = name
    if position is not None or rotation is not None or look_at is not None:
        pos = position
        if look_at is not None:
            src = pos if pos is not None else get_transform(an, "transform", frame, shot)["position"]
            rotation = look_at_angles(src, look_at)
        set_transform(an, position=pos, rotation=rotation, control="transform",
                      frame=frame, shot=shot)
    for ctl, real in (("intensity", intensity), ("horizontalFOV", fov), ("verticalFOV", fov),
                      ("maxDistance", distance), ("radius", softness),
                      ("volumetricIntensity", volumetric_intensity)):
        if real is not None:
            _slider(a, ctl, real=real, frame=frame, shot=shot)
    if color is not None:
        for ch, v in zip(("color_red", "color_green", "color_blue"), color):
            _slider(a, ch, norm=float(v) / 255.0, frame=frame, shot=shot)
    if L is not None:
        if shadows is not None:
            L.SetValue("castsShadows", bool(shadows))
        if volumetric is not None:
            L.SetValue("volumetric", bool(volumetric))
        if texture:
            L.SetValue("texture", str(texture))
    tr = get_transform(an, "transform", frame, shot)
    out = {"light": an, "position": tr["position"], "rotation": tr["rotation"]}
    # Read the sliders: the light element itself updates only on the next
    # evaluation pass, after this call returns.
    out.update(intensity=_slider_real(a, "intensity"), fov=_slider_real(a, "horizontalFOV"),
               distance=_slider_real(a, "maxDistance"), softness=_slider_real(a, "radius"),
               color=[int(round(_slider_real(a, c) * 255))
                      for c in ("color_red", "color_green", "color_blue")])
    if L is not None:
        out.update(shadows=bool(L.GetValue("castsShadows")), volumetric=bool(L.GetValue("volumetric")),
                   texture=_s(L.GetValue("texture")))
    return out


def _slider_real(a, name):
    c = a.FindControl(name)
    v = _num(c.GetValue("value"))
    op = c.GetValue("channel").toElement
    if op is not None and op.GetTypeString() == "DmeExpressionOperator":
        lo, hi = _num(op.GetValue("lo")), _num(op.GetValue("hi"))
        return round(lo + v * (hi - lo), 2)
    return round(v, 3)


def set_frame(frame, shot=None):
    """Move the playhead to a shot frame (safe from the bridge timer)."""
    sh = _shot(shot)
    _m("sfmApp").SetHeadTimeInSeconds(_secs(sh.timeFrame.start) + float(frame) / _fps())
    return {"frame": head_frame(sh)}


def viewport_rect():
    """Primary viewport as fractions [x0, y0, x1, y1] of the main window's
    frame (DPI-independent, used to crop screenshots)."""
    from PySide import QtCore, QtGui
    best = None
    for w in QtGui.QApplication.instance().allWidgets():
        if w.metaObject().className() == "CQViewportArea" and w.isVisible():
            if best is None or w.width() * w.height() > best.width() * best.height():
                best = w
    if best is None:
        return None
    fg = _wrap(best.window(), QtGui.QWidget).frameGeometry()
    p = best.mapToGlobal(QtCore.QPoint(0, 0))
    fx, fy, fw, fh = fg.x(), fg.y(), float(fg.width()), float(fg.height())
    return [(p.x() - fx) / fw, (p.y() - fy) / fh,
            (p.x() + best.width() - fx) / fw, (p.y() + best.height() - fy) / fh]


def joints(animset, bones, frame=None, shot=None):
    """Character-space positions (inches; X fwd, Y left, Z up) of bone
    joints at a frame, to check a pose numerically."""
    sh = _shot(shot)
    a = _animset(animset, sh)
    rig = _rig(a)
    fr = head_frame(sh) if frame is None else int(frame)
    wp, _ = _fk(rig, _local_pose(rig, _time(sh, a, fr)))
    return {"frame": fr, "joints": dict((n, [round(x, 1) for x in wp[_bone(rig, n)]])
                                        for n in bones)}


# ------------------------------------------------- animation-set script context
# sfm.GetCurrentShot / CreateModel / CreateAnimationSet only work inside a
# script started from the Animation Set Editor's right-click menu. ctx_start
# opens that menu on an animation set row (posted event, like a user click),
# then picks Rig > sfm_mcp_context from the event loop; that script calls
# run_ctx_pending(), which runs the queued code with the context available.
_CTX = {"state": None}
CTX_SCRIPT = "sfm_mcp_context"


def _qt():
    from PySide import QtCore, QtGui, shiboken
    return QtCore, QtGui, shiboken


def _wrap(obj, cls):
    _, _, shiboken = _qt()
    return shiboken.wrapInstance(long(shiboken.getCppPointer(obj)[0]), cls)


def _animset_tree():
    QtCore, QtGui, _ = _qt()
    for w in QtGui.QApplication.instance().allWidgets():
        if w.metaObject().className() == "CQAnimationSetControlTree" and w.isVisible():
            return _wrap(w, QtGui.QTreeView)
    return None


def ctx_start(code, label=None, animset=None):
    """Queue python code to run inside SFM's animation-set script context
    (sfm.GetCurrentShot() etc. work there). Poll ctx_poll() for the result."""
    import time
    QtCore, QtGui, _ = _qt()
    st = _CTX.get("state")
    if st in ("menu", "running") and time.time() - _CTX.get("t0", 0) < 30:
        raise ValueError("another context job is still running")
    if QtGui.QApplication.instance().activeModalWidget() is not None:
        raise ValueError("a modal dialog is open in SFM; close it first")
    tv = _animset_tree()
    if tv is None:
        raise ValueError("Animation Set Editor tree is not visible")
    m = tv.model()
    names = [_s(m.data(m.index(r, 0))) for r in range(m.rowCount())]
    if not names:
        raise ValueError("the shot has no animation sets yet: add any element "
                         "first (sfm_light or sfm_camera with create=True) so the menu exists")
    row = names.index(animset) if animset in names else 0
    idx = m.index(row, 0)
    r = tv.visualRect(idx)
    if not tv.viewport().rect().contains(r.center()):
        tv.scrollTo(idx)
        r = tv.visualRect(idx)
    pos = QtCore.QPoint(r.x() + min(24, r.width() // 2), r.center().y())
    vp = tv.viewport()
    _CTX.clear()
    _CTX.update(state="menu", code=code, label=label, t0=time.time(), row=names[row])
    QtCore.QTimer.singleShot(120, lambda: _ctx_pick(0))
    QtGui.QApplication.instance().postEvent(
        vp, QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, pos, vp.mapToGlobal(pos)))
    return {"queued": True, "row": names[row]}


def _ctx_find(menu):
    for a in menu.actions():
        sub = a.menu()
        if sub is not None and _s(a.text()).replace("&", "") == "Rig":
            for b in sub.actions():
                if _s(b.text()) == CTX_SCRIPT:
                    return b
    return None


def _ctx_pick(tries):
    QtCore, QtGui, _ = _qt()
    try:
        if _CTX.get("state") != "menu":
            return
        p = QtGui.QApplication.instance().activePopupWidget()
        if p is None:
            if tries < 30:
                QtCore.QTimer.singleShot(100, lambda: _ctx_pick(tries + 1))
            else:
                _CTX.update(state="error", error="the animation set context menu did not open")
            return
        menu = _wrap(p, QtGui.QMenu)
        act = _ctx_find(menu)
        if act is None:
            _CTX.update(state="error", error="Rig > %s is missing; run install_bridge.py "
                        "(or Scripts > Rescan Script Menus)" % CTX_SCRIPT)
        else:
            QtCore.QMetaObject.invokeMethod(act, "trigger", QtCore.Qt.QueuedConnection)
        QtCore.QMetaObject.invokeMethod(menu, "close", QtCore.Qt.QueuedConnection)
    except Exception as e:
        _CTX.update(state="error", error="menu pick failed: %s" % e)


def run_ctx_pending():
    """Called by the Rig > sfm_mcp_context script (inside the context)."""
    if _CTX.get("state") != "menu":
        return False
    _CTX["state"] = "running"
    import sfm_mcp_bridge as B
    B._state["busy"] = True
    try:
        resp = B._exec_code(_CTX["code"], True, _CTX.get("label"))
    except Exception as e:
        resp = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        B._state["busy"] = False
    _CTX.update(state="done", resp=resp)
    return True


def ctx_poll():
    """{'state': 'menu'|'running'} while waiting, else the exec response."""
    import time
    st = _CTX.get("state")
    if st in ("menu", "running"):
        if time.time() - _CTX.get("t0", 0) > 30:
            _CTX.update(state="error", error="context script did not run within 30 s")
        else:
            return {"state": st}
    if st == "done":
        resp = _CTX.get("resp")
    elif st == "error":
        resp = {"ok": False, "error": _CTX.get("error")}
    else:
        resp = {"ok": False, "error": "no context job"}
    _CTX.clear()
    _CTX["state"] = None
    return resp


def _mdl_path(model):
    p = _s(model).replace("\\", "/").strip("/")
    if not p.lower().startswith("models/"):
        p = "models/" + p
    if not p.lower().endswith(".mdl"):
        p += ".mdl"
    return p


def _unique_name(sh, base):
    used = set(_s(a.GetName()) for a in sh.animationSets)
    if base not in used:
        return base
    i = 1
    while "%s%d" % (base, i) in used:
        i += 1
    return "%s%d" % (base, i)


def create_model(model, name=None, position=None, rotation=None, skin=None):
    """Context-only (run through ctx_start): add an .mdl to the current
    shot as a new animation set, like Add > Create Animation Set for New
    Model. position/rotation set rootTransform (static)."""
    sfm = _m("sfm")
    vs = _m("vs")
    sh = sfm.GetCurrentShot()
    if sh is None:
        raise ValueError("create_model needs the script context (ctx_start)")
    path = _mdl_path(model)
    if not vs.g_pFullFileSystem.FileExists(path, "GAME"):
        raise ValueError("model not found: %s (use sfm_find_assets)" % path)
    base = _s(name) if name else path.rsplit("/", 1)[-1][:-4] + "1"
    an = _unique_name(sh, base) if not name else base
    if name and an in [_s(a.GetName()) for a in sh.animationSets]:
        raise ValueError("animation set %r already exists" % an)
    gm = sfm.CreateModel(path)
    if gm is None:
        raise ValueError("SFM could not create the model %s" % path)
    if skin is not None:
        gm.SetValue("skin", int(skin))
    a = sfm.CreateAnimationSet(an, target=gm)
    if a is None:
        raise ValueError("SFM could not create the animation set")
    an = _s(a.GetName())
    dag = vs.CreateElement("DmeDag", an, sh.GetFileId())   # as sfmUtils does
    dag.AddChild(gm)
    sh.scene.AddChild(dag)
    if position is not None or rotation is not None:
        set_transform(an, position=position, rotation=rotation, shot=_s(sh.GetName()))
    return {"animset": an, "model": path, "controls": len(a.controls)}


# -------------------------------------------------------------------- undo
def undo_state():
    dm = _m("vs").g_pDataModel
    return {"undo": _s(dm.GetUndoDesc()) if dm.CanUndo() else None,
            "redo": _s(dm.GetRedoDesc()) if dm.CanRedo() else None}


def undo(steps=1, redo=False):
    """Queue Edit > Undo (or Redo) `steps` times, exactly like Ctrl+Z."""
    QtCore, QtGui, _ = _qt()
    if QtGui.QApplication.instance().activeModalWidget() is not None:
        raise ValueError("a modal dialog is open in SFM; close it first")
    act = _main_action(u"Redo" if redo else u"Undo")
    if act is None:
        raise ValueError("Edit > Undo not found")
    st = undo_state()
    n = max(1, min(int(steps), 50))
    for _ in range(n):
        QtCore.QMetaObject.invokeMethod(act, "trigger", QtCore.Qt.QueuedConnection)
    return {"queued": n, "redo" if redo else "undo": st["redo" if redo else "undo"]}


# -------------------------------------------------------------------- save
def save_copy(path, overwrite=False):
    """Write a copy of the open session to a NEW .dmx file. The open
    document keeps its own file name and unsaved state (the user's file is
    never written). overwrite=True only replaces an earlier copy."""
    import os
    vs, sfmApp = _m("vs"), _m("sfmApp")
    root = sfmApp.GetDocumentRoot()
    if root is None:
        raise ValueError("no document is open")
    p = os.path.abspath(_s(path))
    if not p.lower().endswith(".dmx"):
        p += ".dmx"
    cur = _s(vs.g_pDataModel.GetFileName(root.GetFileId()))
    if cur and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(p):
        raise ValueError("refusing to write over the open session file; use another name "
                         "(the user saves their own file with Ctrl+S)")
    if os.path.exists(p) and not overwrite:
        raise ValueError("%s exists; pass overwrite=True to replace it" % p)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    if not vs.g_pDataModel.SaveToFile(p, None, "binary", "sfm_session", root):
        raise ValueError("SFM failed to write %s" % p)
    return {"saved": p, "bytes": os.path.getsize(p), "open_document": cur}


# ------------------------------------------------------------------ render
# Drives SFM's own File > Export > Movie... dialog (CQMovieLayoffSettingsDialog,
# plain Qt widgets) step by step from QTimer callbacks, so the bridge call
# returns at once and SFM keeps its event loop. The "save changes?" box that
# appears for a modified document is answered "Continue without Saving" (the
# user's file is never saved by this).
_RENDER = {"state": None}
_EXPORT_TYPES = {"movie": "Movie", "images": "Image Sequence", "sound": "Sound"}


def _main_action(text):
    QtCore, QtGui, shiboken = _qt()
    for x in _m("sfmApp").GetMainWindow().findChildren(QtGui.QAction):
        if shiboken.isValid(x) and x.text() == text:
            return x
    return None


def _combo_with(dlg, item, visible=True):
    QtCore, QtGui, _ = _qt()
    for c in dlg.findChildren(QtGui.QComboBox):
        if visible and not c.isVisible():
            continue
        items = [_s(c.itemText(i)) for i in range(c.count())]
        if item in items:
            return c, items
    return None, None


def _pick(combo, items, text):
    if combo is None:
        return False
    for i, t in enumerate(items):
        if t.lower().startswith(_s(text).lower()):
            combo.setCurrentIndex(i)
            return True
    raise ValueError("%r is not one of %s" % (text, items))


def render_start(path, kind="images", fmt=None, codec=None, width=None, height=None,
                 start=None, end=None, dof=None, motion_blur=None, antialias=None,
                 ambient_occlusion=None, audio=None):
    """Start an export through SFM's Export Movie dialog. kind images|movie|
    sound; fmt PNG|JPEG|TGA (images) or MP4|MOV|AVI (movie); start/end are
    shot frames (end inclusive). Poll render_poll()."""
    import time
    QtCore, QtGui, _ = _qt()
    if _RENDER.get("state") in ("opening", "rendering"):
        raise ValueError("a render is already running")
    if kind not in _EXPORT_TYPES:
        raise ValueError("kind must be one of %s" % sorted(_EXPORT_TYPES))
    if QtGui.QApplication.instance().activeModalWidget() is not None:
        raise ValueError("a modal dialog is open in SFM; close it first")
    act = _main_action(u"Movie...")
    if act is None:
        raise ValueError("File > Export > Movie... not found")
    sh = _shot()
    off = _secs(sh.timeFrame.start)
    _RENDER.clear()
    fmt = fmt or {"images": "PNG", "movie": "MP4"}.get(kind)
    _RENDER.update(state="opening", t0=time.time(), path=_s(path), kind=kind, fmt=fmt,
                   codec=codec, width=width, height=height, dof=dof, mb=motion_blur,
                   aa=antialias, ao=ambient_occlusion, fps=_fps(), off=off,
                   audio=(False if audio is None and kind == "images" else audio),
                   start=start, end=end)
    _RENDER["saved"] = [_snapshot(el) for el in _export_settings()]
    QtCore.QMetaObject.invokeMethod(act, "trigger", QtCore.Qt.QueuedConnection)
    QtCore.QTimer.singleShot(200, lambda: _render_step(0))
    return {"started": True, "path": _s(path)}


def _export_settings():
    """The session elements the Export Movie dialog writes its choices to."""
    st = _m("sfmApp").GetDocumentRoot().GetValue("settings")
    out = []
    for name in ("movieSettings", "renderSettings"):
        el = st.GetValue(name) if st is not None else None
        if el is not None:
            out.append(el)
            if name == "renderSettings" and el.GetValue("ProgressiveRefinement") is not None:
                out.append(el.GetValue("ProgressiveRefinement"))
    return out


def _snapshot(el):
    vals = {}
    a = el.FirstAttribute()
    while a:
        n = _s(a.GetName())
        if n != "name" and a.GetTypeString() in ("bool", "int", "float", "string"):
            vals[n] = el.GetValue(n)
        a = a.NextAttribute()
    return vals


def render_restore():
    """Put the Export Movie settings back as they were before render_start
    (run inside an undo scope)."""
    saved = _RENDER.pop("saved", None)
    if not saved:
        return {"restored": 0}
    n = 0
    for el, vals in zip(_export_settings(), saved):
        for k, v in vals.items():
            if el.GetValue(k) != v:
                el.SetValue(k, _bytes(v) if isinstance(v, unicode) else v)
                n += 1
    return {"restored": n}


def _render_fill(dlg):
    QtCore, QtGui, _ = _qt()
    R = _RENDER
    c, items = _combo_with(dlg, "Image Sequence")
    _pick(c, items, _EXPORT_TYPES[R["kind"]])
    if R["kind"] == "images":
        c, items = _combo_with(dlg, "PNG")
        if R["fmt"]:
            _pick(c, items, R["fmt"])
    elif R["kind"] == "movie":
        c, items = _combo_with(dlg, "MP4")
        if R["fmt"]:
            _pick(c, items, R["fmt"])
        c, items = _combo_with(dlg, "H.264")
        if R["codec"] and c is not None:
            _pick(c, items, R["codec"])
    edits = [e for e in dlg.findChildren(QtGui.QLineEdit) if e.isVisible()]
    if len(edits) < 5:
        raise ValueError("unexpected Export Movie dialog layout (%d line edits)" % len(edits))
    edits[0].setText(R["path"])
    if R["width"] or R["height"]:
        c, items = _combo_with(dlg, "720p")
        _pick(c, items, "Custom")
        if R["width"]:
            edits[1].setText(str(int(R["width"])))
            edits[1].editingFinished.emit()
        if R["height"]:
            edits[2].setText(str(int(R["height"])))
            edits[2].editingFinished.emit()
    if R["start"] is not None or R["end"] is not None:
        c, items = _combo_with(dlg, "Selected Shots")
        _pick(c, items, "Custom")
        c, items = _combo_with(dlg, "Frames")
        _pick(c, items, "Seconds")
        fps, off = R["fps"], R["off"]
        s = off + float(R["start"] or 0) / fps
        e = off + float(R["end"] + 1 if R["end"] is not None else 0) / fps
        if R["end"] is None:
            e = off + _secs(_shot().timeFrame.duration)
        edits[3].setText("%.4f" % s)
        edits[3].editingFinished.emit()
        edits[4].setText("%.4f" % e)
        edits[4].editingFinished.emit()
    for label, val in (("Depth Of Field", R["dof"]), ("Motion Blur", R["mb"]),
                       ("Subpixel Jitter Antialiasing", R["aa"]),
                       ("Ambient Occlusion", R["ao"]),
                       ("Separate WAV file" if R["kind"] == "images" else "Audio", R["audio"])):
        if val is None:
            continue
        for cb in dlg.findChildren(QtGui.QCheckBox):
            if _s(cb.text()) == label:
                cb.setChecked(bool(val))
    R["settings"] = {"path": _s(edits[0].text()), "w": _s(edits[1].text()),
                     "h": _s(edits[2].text()), "start_s": _s(edits[3].text()),
                     "end_s": _s(edits[4].text())}


def _render_step(tries):
    import time
    QtCore, QtGui, _ = _qt()
    R = _RENDER
    try:
        if R.get("state") != "opening":
            return
        md = QtGui.QApplication.instance().activeModalWidget()
        cls = md.metaObject().className() if md is not None else None
        if cls == "QMessageBox":
            box = _wrap(md, QtGui.QMessageBox)
            btn = [b for b in box.findChildren(QtGui.QPushButton)
                   if _s(b.text()) == "Continue without Saving"]
            if not btn:
                R.update(state="error", error="unexpected message box: %s"
                         % _s(box.text()))
                return
            QtCore.QMetaObject.invokeMethod(btn[0], "click", QtCore.Qt.QueuedConnection)
        elif cls == "CQMovieLayoffSettingsDialog":
            dlg = _wrap(md, QtGui.QDialog)
            try:
                _render_fill(dlg)
            except Exception as e:
                QtCore.QMetaObject.invokeMethod(md, "OnCancel", QtCore.Qt.QueuedConnection)
                R.update(state="error", error="dialog: %s" % e)
                return
            R.update(state="rendering", t_render=time.time())
            QtCore.QMetaObject.invokeMethod(md, "OnExport", QtCore.Qt.QueuedConnection)
            return
        if tries > 60:
            R.update(state="error", error="Export Movie dialog did not open (modal: %s)" % cls)
            return
        QtCore.QTimer.singleShot(150, lambda: _render_step(tries + 1))
    except Exception as e:
        R.update(state="error", error="render step failed: %s" % e)


def render_poll():
    """State of the export: opening | rendering | done | error, plus the
    dialog/settings used. 'done' = no SFM modal dialog left open."""
    QtCore, QtGui, _ = _qt()
    R = _RENDER
    st = R.get("state")
    out = {"state": st}
    if st == "rendering":
        md = QtGui.QApplication.instance().activeModalWidget()
        if md is None:
            R["state"] = st = "done"
        else:
            out["modal"] = md.metaObject().className()
    out["state"] = st
    for k in ("error", "settings", "path", "kind"):
        if k in R:
            out[k] = R[k]
    return out


PUBLIC = ("head_frame", "animsets", "controls", "get_transform", "keys",
          "set_transform", "set_control", "camera", "look_at_angles",
          "sequences", "apply_animation", "pose", "animate", "joints",
          "set_frame", "viewport_rect", "add_element", "light", "create_model",
          "render_poll", "shift", "save_copy",
          "undo", "undo_state", "render_start", "ctx_start", "ctx_poll",
          "render_restore")
