"""The real bridge under SFM's own Python 2.7 and Qt (tests/fake_sfm_qt.py),
with a throwaway home folder and port. Skipped when SFM is not installed.

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import sfm_mcp_server  # noqa: E402


def _py27():
    g = sfm_mcp_server.sfm_game_dir()
    p = g / "sdktools" / "python" / "2.7" / "win32" / "python.exe" if g else None
    return p if p and p.is_file() else None


@unittest.skipUnless(_py27(), "SFM's Python 2.7 not found")
class Bridge(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()  # short path: Python 2.7 has MAX_PATH limits
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home,
                   SFM_MCP_PORT=str(self.port))
        self.proc = subprocess.Popen([str(_py27()), "-B", str(ROOT / "tests" / "fake_sfm_qt.py")],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        token = Path(self.home, ".sfm_mcp", "token")
        for _ in range(100):
            if token.is_file() and self.call("ping").get("ok"):
                break
            time.sleep(0.1)
        else:
            self.fail("bridge did not start")

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.home, ignore_errors=True)

    def call(self, cmd, **args):
        try:
            tok = Path(self.home, ".sfm_mcp", "token").read_text().strip()
            with socket.create_connection(("127.0.0.1", self.port), 5) as s:
                s.settimeout(10)
                s.sendall((json.dumps({"id": 1, "token": tok, "cmd": cmd, "args": args}) + "\n").encode())
                return json.loads(s.makefile().readline() or "{}")
        except OSError:
            return {}

    def test_exec(self):
        r = self.call("exec", code="x = 20\nx + 1", undo=False)
        self.assertEqual((r["ok"], r["result"]), (True, 21))

    def test_system_exit_does_not_end_the_host(self):
        for code in ("raise SystemExit(3)", "import sys; sys.exit()", "exit()"):
            r = self.call("exec", code=code, undo=False)
            self.assertFalse(r.get("ok", True), code)
            self.assertIn("SystemExit", r.get("error", ""), code)
            self.assertIsNone(self.proc.poll(), "bridge process exited on %r" % code)
        self.assertTrue(self.call("ping").get("ok"))


if __name__ == "__main__":
    unittest.main()
