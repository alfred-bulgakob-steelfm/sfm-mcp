# -*- coding: utf-8 -*-
"""Offline harness (run with SFM's Python 2.7): real Qt event loop + a fake
Scripts-menu action, to test the bridge's deferred exec_ctx flow without SFM.

    <SFM>/game/sdktools/python/2.7/win32/python.exe tests/fake_sfm_qt.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "sfm_side"))

from PySide import QtCore, QtGui  # noqa: E402

import sfm_mcp_bridge as B  # noqa: E402

app = QtGui.QApplication(sys.argv)
win = QtGui.QMainWindow()
menu = QtGui.QMenu("Scripts", win)
sub = menu.addMenu("sfm-mcp")
act = sub.addAction("sfm mcp context")
calls = {"n": 0}


def _menu_script():
    # Like SFM: the action runs a script file that calls run_pending().
    calls["n"] += 1
    B.run_pending()


act.triggered.connect(_menu_script)
B._find_context_action = lambda: act
B.start()
print "fake SFM ready; ctx action wired"
sys.stdout.flush()
app.exec_()
