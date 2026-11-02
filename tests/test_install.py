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
MARK = install_bridge.HOOK_MARK.encode()

# Existing sfm_init.py contents that must come back byte for byte.
USER_FILES = {
    "empty": b"",
    "lf": b"import sfm\nprint 'user init'\n",
    "crlf": b"import sfm\r\nprint 'user init'\r\n",
    "mixed": b"a = 1\r\nb = 2\nc = 3\r\n",
    "not_utf8": b"# -*- coding: cp1252 -*-\r\n# caf\xe9 \x93quoted\x94\r\nx = '\xe9'\r\n",
    "trailing_blank_lines": b"x = 1\n\n\n",
    "user_script": b"def setup():\n    import sfmApp\n    return sfmApp\n\nsetup()\n",
}


def _fake_sfm(base: Path) -> Path:
    game = base / "SourceFilmmaker" / "game"
    (game / "usermod" / "scripts" / "sfm").mkdir(parents=True)
    (game / "sfm.exe").write_bytes(b"")
    return game


def _py27():
    g = sfm_mcp_server.sfm_game_dir()
    p = g / "sdktools" / "python" / "2.7" / "win32" / "python.exe" if g else None
    return p if p and p.is_file() else None


def _ansi_ok(path) -> bool:
    """Can SFM's Python 2.7 (ANSI code page file names) import from path?"""
    try:
        str(path).encode("mbcs")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class HookBlock(unittest.TestCase):
    side = Path("C:\\Users\\\u0418\u0432\u0430\u043d \U0001F3AC\\sfm-mcp\\sfm_side")

    def test_non_ascii_path_gives_ascii_block(self):
        for eol in ("\n", "\r\n"):
            with mock.patch.object(install_bridge, "SFM_SIDE", self.side):
                block = install_bridge.hook_block(eol)
            self.assertTrue(block.isascii())
            self.assertEqual(block.count("\n"), block.count(eol))  # one line-ending style
            line = next(x for x in block.splitlines() if "_sfmmcp_dir = u" in x)
            self.assertEqual(ast.literal_eval(line.split("=", 1)[1].strip()), str(self.side))


@unittest.skipUnless(_py27(), "SFM's Python 2.7 not found")
class HookUnderPython27(unittest.TestCase):
    """Load a generated sfm_init.py the way SFM's Python 2.7 loads files
    (execfile applies PEP 263; compile() on bytes would not) with a stub
    bridge in the install folder."""

    def run_hook(self, folder: str, eol: str):
        with tempfile.TemporaryDirectory() as d:  # short path: Python 2.7 MAX_PATH
            side = Path(d, folder, "sfm_side")
            side.mkdir(parents=True)
            (side / "sfm_mcp_bridge.py").write_bytes(b"def start():\n    print 'BRIDGE STARTED'\n")
            init = Path(d, "sfm_init.py")
            with mock.patch.object(install_bridge, "SFM_SIDE", side):
                init.write_bytes(install_bridge.add_hook("print 'USER INIT'" + eol).encode("latin-1"))
            r = subprocess.run([str(_py27()), "-B", "-c", "execfile(r'%s')" % init],
                               capture_output=True, text=True)
            return side, r

    def check(self, folder):
        for eol in ("\n", "\r\n"):
            side, r = self.run_hook(folder, eol)
            self.assertEqual(r.returncode, 0, r.stderr)        # the file itself always loads
            self.assertIn("USER INIT", r.stdout)                # user code still runs
            # The bridge starts iff Python 2.7 can import from that folder;
            # otherwise the hook prints a traceback and SFM carries on.
            self.assertEqual("BRIDGE STARTED" in r.stdout, _ansi_ok(side), r.stderr)

    def test_ascii_path(self):
        self.check("sfm-mcp")

    def test_path_in_ansi_code_page(self):
        self.check("caf\u00e9")   # representable in cp1252 and most Western code pages

    def test_path_outside_ansi_code_page(self):
        self.check("\u0418\u0432\u0430\u043d \U0001F3AC")  # emoji: no ANSI code page has it


class InstallBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.game = _fake_sfm(self.tmp)
        self.init = self.game / "usermod" / "scripts" / "sfm" / "sfm_init.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, *args):
        r = subprocess.run([sys.executable, str(ROOT / "install_bridge.py"), "--sfm-dir",
                            str(self.game), *args], capture_output=True, text=True)
        return r

    def test_install_reinstall_uninstall_keep_user_bytes(self):
        for name, orig in USER_FILES.items():
            with self.subTest(name):
                self.init.write_bytes(orig)
                self.assertEqual(self.bridge().returncode, 0)
                installed = self.init.read_bytes()
                self.assertTrue(installed.startswith(orig))
                self.assertEqual(installed.count(MARK), 1)
                if b"\r\n" in orig:  # the block follows the file's CRLF style
                    self.assertNotIn(b"\n", installed[len(orig):].replace(b"\r\n", b""))
                self.assertEqual(self.bridge().returncode, 0)
                self.assertEqual(self.init.read_bytes(), installed)   # reinstall is stable
                self.assertEqual(self.bridge("--uninstall").returncode, 0)
                self.assertEqual(self.init.read_bytes(), orig)

    def test_no_trailing_newline_gains_only_a_line_end(self):
        for orig, eol in ((b"x = 1", b"\n"), (b"a = 1\r\nb = 2", b"\r\n")):
            self.init.write_bytes(orig)
            self.bridge()
            self.assertTrue(self.init.read_bytes().startswith(orig + eol + MARK))
            self.bridge("--uninstall")
            self.assertEqual(self.init.read_bytes(), orig + eol)

    def test_missing_file(self):
        self.assertEqual(self.bridge().returncode, 0)
        self.assertTrue(self.init.read_bytes().startswith(MARK))
        self.assertEqual(self.bridge("--uninstall").returncode, 0)
        self.assertEqual(self.init.read_bytes(), b"")

    def test_uninstall_without_hook_leaves_file_alone(self):
        self.init.write_bytes(USER_FILES["not_utf8"])
        self.assertEqual(self.bridge("--uninstall").returncode, 0)
        self.assertEqual(self.init.read_bytes(), USER_FILES["not_utf8"])

    def test_upgrade_from_previous_block_format(self):
        # 0.4.0 wrote "\n<block>\n" after rstrip-ing the file.
        old = (b"x = 1\n\n" + MARK + b"\ntry:\n    pass\nexcept Exception:\n    pass\n"
               + install_bridge.END_MARK.encode() + b"\n")
        self.init.write_bytes(old)
        self.assertEqual(self.bridge().returncode, 0)
        new = self.init.read_bytes()
        self.assertEqual(new.count(MARK), 1)
        self.assertIn(b"sfm_mcp_bridge.start()", new)
        self.assertEqual(self.bridge("--uninstall").returncode, 0)
        self.assertEqual(self.init.read_bytes(), b"x = 1\n\n")

    def test_missing_end_marker_leaves_file_alone(self):
        broken = MARK + b"\nold hook\n# user code after it\n"
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
            self.assertNotIn(MARK, init.read_bytes())


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
