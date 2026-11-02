"""Offline installer tests: a throwaway fake SFM folder, never a real install.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
import install_bridge  # noqa: E402
import sfm_mcp_server  # noqa: E402

VPY = ROOT / ".venv" / "Scripts" / "python.exe"
USER_INIT = b"# caf\xe9 (cp1252 byte, not UTF-8)\r\nprint 'user init'\n"


def _fake_sfm(base: Path) -> Path:
    game = base / "SourceFilmmaker" / "game"
    (game / "usermod" / "scripts" / "sfm").mkdir(parents=True)
    (game / "sfm.exe").write_bytes(b"")
    return game


def _py27():
    g = sfm_mcp_server.sfm_game_dir()
    p = g / "sdktools" / "python" / "2.7" / "win32" / "python.exe" if g else None
    return p if p and p.is_file() else None


class HookBlock(unittest.TestCase):
    side = Path("C:\\Users\\\u0418\u0432\u0430\u043d \U0001F3AC\\sfm-mcp\\sfm_side")

    def test_non_ascii_path_gives_ascii_block(self):
        with mock.patch.object(install_bridge, "SFM_SIDE", self.side):
            block = install_bridge.hook_block()
        self.assertTrue(block.isascii())
        line = next(x for x in block.splitlines() if "_sfmmcp_dir = u" in x)
        self.assertEqual(ast.literal_eval(line.split("=", 1)[1].strip()), str(self.side))

    @unittest.skipUnless(_py27(), "SFM's Python 2.7 not found")
    def test_python27_compiles_block(self):
        with mock.patch.object(install_bridge, "SFM_SIDE", self.side):
            block = install_bridge.hook_block()
        code = "compile(open(r'%s', 'rb').read(), 'sfm_init.py', 'exec')"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "init.py")
            f.write_bytes(USER_INIT + block.encode("ascii"))
            r = subprocess.run([str(_py27()), "-B", "-c", code % f], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class InstallBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = _fake_sfm(self.tmp)
        self.init = self.game / "usermod" / "scripts" / "sfm" / "sfm_init.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "install_bridge.py"), "--sfm-dir",
                               str(self.game), *args], capture_output=True, text=True)

    def test_round_trip_keeps_user_bytes(self):
        self.init.write_bytes(USER_INIT.replace(b"\r\n", b"\n"))
        self.assertEqual(self.bridge().returncode, 0)
        self.assertIn(install_bridge.HOOK_MARK.encode(), self.init.read_bytes())
        self.assertEqual(self.bridge().returncode, 0)  # reinstall replaces the block
        self.assertEqual(self.init.read_bytes().count(install_bridge.HOOK_MARK.encode()), 1)
        self.assertEqual(self.bridge("--uninstall").returncode, 0)
        self.assertEqual(self.init.read_bytes(), USER_INIT.replace(b"\r\n", b"\n"))

    def test_missing_end_marker_leaves_file_alone(self):
        broken = (install_bridge.HOOK_MARK + "\nold hook\n# user code after it\n").encode()
        self.init.write_bytes(broken)
        for args in ((), ("--uninstall",)):
            r = self.bridge(*args)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertEqual(self.init.read_bytes(), broken)


@unittest.skipUnless(os.name == "nt" and VPY.is_file(), "needs Windows and .venv")
class InstallPyUninstall(unittest.TestCase):
    def run_install(self, game, home, *args):
        env = dict(os.environ, USERPROFILE=str(home),
                   PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ["SystemRoot"])
        env.pop("SFM_GAME_DIR", None)
        return subprocess.run([sys.executable, str(ROOT / "install.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_uninstall_honours_sfm_dir(self):
        with tempfile.TemporaryDirectory() as d:
            game, home = _fake_sfm(Path(d)), Path(d, "home")
            home.mkdir()  # no claude.exe here: the user's Claude config is not touched
            init = game / "usermod" / "scripts" / "sfm" / "sfm_init.py"
            subprocess.run([sys.executable, str(ROOT / "install_bridge.py"), "--sfm-dir",
                            str(game)], check=True, capture_output=True)
            r = self.run_install(game, home, "--uninstall", "--sfm-dir", str(game))
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn(install_bridge.HOOK_MARK, init.read_text(encoding="latin-1"))


class SteamLibraries(unittest.TestCase):
    def test_utf8_library_path(self):
        with tempfile.TemporaryDirectory() as d:
            steam, lib = Path(d, "Steam"), Path(d, "\u0418\u0433\u0440\u044b")
            (steam / "steamapps").mkdir(parents=True)
            (lib / "steamapps" / "common").mkdir(parents=True)
            game = _fake_sfm(lib / "steamapps" / "common")
            vdf = '"libraryfolders"\n{\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % str(lib).replace("\\", "\\\\")
            (steam / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")

            class Key:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            winreg = types.SimpleNamespace(HKEY_CURRENT_USER=0, OpenKey=lambda *a: Key(),
                                           QueryValueEx=lambda k, n: (str(steam), 1))
            with mock.patch.dict(sys.modules, {"winreg": winreg}), \
                    mock.patch.dict(os.environ, {"SFM_GAME_DIR": ""}):
                self.assertEqual(sfm_mcp_server.sfm_game_dir(), game)


if __name__ == "__main__":
    unittest.main()
