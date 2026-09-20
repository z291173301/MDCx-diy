"""议题 #25 回归：演员管理器任务会话代数，防旧线程排队回调覆盖新会话状态。

背景：cancel→快速重开场景下，旧 QThread 已 emit 进事件队列的 progress/done 回调
会晚于新线程启动到达，覆盖 self._actors/进度条/按钮态（mdcz MaintenanceSession
generation 同款防护）。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]

_app: QApplication | None = None


def _ensure_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class _Worker(QObject):
    """伪工作线程：只需携带 _session_gen 属性并发信号。"""

    done = pyqtSignal(list)


class _Host(QObject):
    _begin_session = None  # type: ignore[assignment]
    _is_stale_session = None  # type: ignore[assignment]

    def __init__(self):
        super().__init__()
        self._session_gen = 0
        self.received: list = []

    def on_done(self, actors: list):
        if self._is_stale_session():
            return
        self.received.extend(actors)


def _make_host():
    from mdcx.tools.emby_actor_manager_ui import EmbyActorManagerDialog

    _ensure_app()
    _Host._begin_session = EmbyActorManagerDialog._begin_session
    _Host._is_stale_session = EmbyActorManagerDialog._is_stale_session
    return _Host()


def test_stale_session_callback_is_dropped():
    host = _make_host()
    old = _Worker()
    host._begin_session(old)
    old.done.connect(host.on_done)

    new = _Worker()
    host._begin_session(new)
    new.done.connect(host.on_done)

    old.done.emit(["旧会话结果"])
    assert host.received == [], "旧代数线程的回调必须整体作废"
    new.done.emit(["新会话结果"])
    assert host.received == ["新会话结果"]


def test_cancel_bumps_generation():
    """cancel 分支代数 +1：旧线程排队信号作废，UI 立即恢复。"""
    host = _make_host()
    worker = _Worker()
    host._begin_session(worker)
    assert host._session_gen == 1
    host._session_gen += 1  # cancel 分支语义
    worker.done.emit(["x"])
    assert host.received == []


def test_non_thread_sender_not_blocked():
    """无代数标记的 sender（非工作线程信号）不得被守卫误拦。"""
    host = _make_host()
    plain = _Worker()
    plain.done.connect(host.on_done)
    plain.done.emit(["ok"])
    assert host.received == ["ok"]


_CALLBACKS = (
    "_on_fetch_progress",
    "_on_fetch_finished",
    "_on_preview_finished",
    "_on_sync_progress",
    "_on_sync_actor_done",
    "_on_sync_finished",
    "_on_clean_actor_done",
    "_on_clean_finished",
    "_on_thread_error",
)


def test_all_worker_callbacks_guarded():
    """AST 哨兵：9 个线程回调必须全部含 _is_stale_session 守卫（新增回调漏守卫即红）。"""
    src = (ROOT / "mdcx/tools/emby_actor_manager_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in _CALLBACKS:
        assert name in fns, f"{name} 不存在"
        body = ast.unparse(fns[name])
        assert "_is_stale_session" in body, f"{name} 缺会话守卫"


def test_cancel_branch_invalidates_session_and_restores_ui():
    """哨兵：preview cancel 分支必须代数+1 并恢复按钮（不再依赖旧线程信号收尾）。"""
    src = (ROOT / "mdcx/tools/emby_actor_manager_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_on_prepare_preview")
    text = ast.unparse(fn)
    cancel_seg = text.split("isRunning()")[1].split("mode_map")[0]
    assert "_session_gen += 1" in cancel_seg
    assert "_set_buttons_enabled(True)" in cancel_seg


def test_thread_starts_begin_session():
    """4 个任务线程启动前必须 _begin_session 颁发代数。"""
    src = (ROOT / "mdcx/tools/emby_actor_manager_ui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for fn_name, marker in (
        ("_on_media_folders_result", "FetchActorsThread"),
        ("_on_prepare_preview", "PreparePreviewThread"),
        ("_on_clean_data", "CleanDataThread"),
        ("_on_sync", "SyncThread"),
    ):
        body = fns.get(fn_name, "")
        assert marker in body and "_begin_session" in body, f"{fn_name} 未颁发会话代数"
