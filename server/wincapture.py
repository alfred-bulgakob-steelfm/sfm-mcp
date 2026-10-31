"""Capture the SFM main window with Win32 PrintWindow (PW_RENDERFULLCONTENT).

Works when SFM is covered by other windows (not when minimized) and never
touches SFM's Python, so it cannot crash SFM.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io

from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0

try:  # physical pixels on high-DPI screens
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def _pids_of(exe: str) -> set[int]:
    import subprocess

    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def find_main_window(exe: str = "sfm.exe") -> int | None:
    pids = _pids_of(exe)
    if not pids:
        return None
    best = [None, -1]

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best[1]:
                best[0], best[1] = hwnd, area
        return True

    user32.EnumWindows(cb, 0)
    return best[0]


def _encode(img: "Image.Image", fmt: str, quality: int) -> bytes:
    out = io.BytesIO()
    if fmt == "png":
        img.save(out, "PNG", optimize=True)
    else:  # JPEG: 5-10x smaller than PNG for rendered 3D frames
        img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True,
                                progressive=False, subsampling=2)
    return out.getvalue()


def shrink_image(path: str, max_width: int = 768, quality: int = 70) -> bytes:
    """Load an image file (e.g. a render) and return a downscaled JPEG."""
    img = Image.open(path)
    img.load()
    if max_width and img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    return _encode(img, "jpeg", quality)


def capture_window(hwnd: int, max_width: int = 1600,
                   crop: list[float] | None = None, fmt: str = "jpeg",
                   quality: int = 70) -> bytes:
    """crop: [x0, y0, x1, y1] as fractions of the window (e.g. viewport).
    fmt: jpeg (default, small) or png (lossless, for fine UI text)."""
    if user32.IsIconic(hwnd):
        raise RuntimeError("SFM window is minimized; restore it to take a screenshot.")
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        raise RuntimeError("SFM window has no size.")
    hdc_win = user32.GetWindowDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed")
        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth, bih.biHeight = w, -h  # top-down
        bih.biPlanes, bih.biBitCount, bih.biCompression = 1, 32, BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bih), DIB_RGB_COLORS):
            raise RuntimeError("GetDIBits failed")
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(hwnd, hdc_win)
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    if crop:
        x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in crop)
        if x1 > x0 and y1 > y0:
            img = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
            w, h = img.size
    if max_width and w > max_width:
        img = img.resize((max_width, round(h * max_width / w)), Image.LANCZOS)
    return _encode(img, fmt, quality)
