"""sfm_mcp_scene.py helpers under SFM's own Python 2.7, with stand-in DME
objects (no SFM process needed). Skipped when SFM is not installed.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import sfm_mcp_server  # noqa: E402


def _py27():
    g = sfm_mcp_server.sfm_game_dir()
    p = g / "sdktools" / "python" / "2.7" / "win32" / "python.exe" if g else None
    return p if p and p.is_file() else None


CHANNELS_CLIP = r"""
import sys
sys.path.insert(0, SIDE)
import sfm_mcp_scene as S

class El(object):
    def __init__(self, name, **attrs):
        self.name, self.attrs = name, attrs
        self.__dict__.update(attrs)
    def GetName(self): return self.name
    def HasAttribute(self, n): return n in self.attrs
    def GetValue(self, n): return self.attrs[n]
    def GetHandle(self): return id(self)

def clip(name, chans): return El(name, channels=chans)

pos = El("rootTransform_pos")
cam_ctl = El("transform", positionChannel=pos)
other = clip("scout", [El("x")])
cam_clip = clip("camera1", [El("y"), pos])          # created as camera1, renamed later
tg = El("channelTrackGroup", tracks=[El("t", children=[other, cam_clip])])
shot = El("shot1"); shot.FindTrackGroup = lambda n: tg

renamed = El("cam_film", controls=[cam_ctl])
assert S._channels_clip(shot, renamed) is cam_clip, "renamed set must find its own clip"
by_name = El("scout", controls=[El("c", channel=El("z"))])
assert S._channels_clip(shot, by_name) is other, "lookup by name still works"
assert S._channels_clip(shot, El("ghost", controls=[])) is None
print("OK")
"""


@unittest.skipUnless(_py27(), "SFM's Python 2.7 not found")
class ChannelsClip(unittest.TestCase):
    def test_renamed_animset_finds_its_clip(self):
        code = "SIDE = r'%s'\n" % (ROOT / "sfm_side") + CHANNELS_CLIP
        r = subprocess.run([str(_py27()), "-B", "-c", code], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "OK"), r.stderr)


if __name__ == "__main__":
    unittest.main()
