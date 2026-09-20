"""议题 #175 回归：获取数据中关闭演员管理器不得拆掉仍在跑的 QThread。

根因：窗口 WA_DeleteOnClose，工作线程 parent=窗口；closeEvent 只等
_fetch/_preview/_sync 三根，且 FetchActorsThread 没有可打断的 cancel，
executor.run 卡在网络请求上。wait(5000) 超时后仍销毁窗口，Windows 上
「QThread: Destroyed while thread is still running」直接 abort 整个进程，
日志为空，看起来像主程序被一起关掉。

修复：全部工作线程走可取消 Future；关窗等齐五根线程；超时卸父级并保住引用。
"""

from __future__ import annotations

import ast
import os
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QWidget

_app: QApplication | None = None
ROOT = Path(__file__).resolve().parents[1]


def _ensure_app():
    global _app
    _app = QApplication.instance() or QApplication([])
    return _app


def test_close_event_waits_all_worker_thread_attrs():
    src = (ROOT / "mdcx/tools/emby_actor_manager_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_shutdown_worker_threads")
    text = ast.unparse(fn)
    for attr in ("_fetch_thread", "_preview_thread", "_sync_thread", "_clean_thread", "_refresh_thread"):
        assert attr in text, f"关窗收尾漏了 {attr}"
    assert "abort" in text
    assert "_detach_running_thread" in text


def test_fetch_and_preview_threads_can_abort_running_future():
    src = (ROOT / "mdcx/tools/emby_actor_manager_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id == "_CancellableWorkerThread" for b in n.bases)
    }
    for required in (
        "FetchActorsThread",
        "PreparePreviewThread",
        "SyncThread",
        "CleanDataThread",
        "ActorSourceTestThread",
    ):
        assert required in names, f"{required} 必须可取消，关窗才能打断 executor 阻塞"


class _HangThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._gate = threading.Event()
        self.abort_called = False

    def abort(self):
        self.abort_called = True

    def run(self):
        self._gate.wait()

    def release(self):
        self._gate.set()


def test_shutdown_detaches_thread_when_wait_times_out():
    """wait 超时后线程必须卸父级，不能随窗口一起 Destroyed。"""
    _ensure_app()
    from mdcx.tools.emby_actor_manager_ui import _ORPHAN_WORKER_THREADS, EmbyActorManagerDialog

    parent = QWidget()
    hang = _HangThread(parent)
    hang.start()
    assert hang.wait(20) is False
    try:
        ns = SimpleNamespace(
            _fetch_thread=hang,
            _preview_thread=None,
            _sync_thread=None,
            _clean_thread=None,
            _refresh_thread=None,
        )
        EmbyActorManagerDialog._shutdown_worker_threads(ns, timeout_ms=30)
        assert hang.abort_called
        assert hang.parent() is None, "超时后必须卸父级，避免 WA_DeleteOnClose 拆线程"
        assert hang in _ORPHAN_WORKER_THREADS
    finally:
        hang.release()
        hang.wait(2000)
        _ORPHAN_WORKER_THREADS.discard(hang)
        parent.deleteLater()


def test_shutdown_idle_threads_do_not_detach():
    _ensure_app()
    from mdcx.tools.emby_actor_manager_ui import EmbyActorManagerDialog

    parent = QWidget()
    hang = _HangThread(parent)
    hang.release()
    hang.start()
    hang.wait(2000)
    try:
        ns = SimpleNamespace(
            _fetch_thread=hang,
            _preview_thread=None,
            _sync_thread=None,
            _clean_thread=None,
            _refresh_thread=None,
        )
        EmbyActorManagerDialog._shutdown_worker_threads(ns, timeout_ms=200)
        assert hang.parent() is parent, "已结束的线程不必卸父级"
    finally:
        parent.deleteLater()
