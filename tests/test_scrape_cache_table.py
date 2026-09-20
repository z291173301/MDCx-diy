"""议题 #179 回归：刮削缓存失败列表列宽必须随视口伸缩，不出现横向滚动条。

旧实现列宽固定 5×130=650（Interactive）+ 刷新时 resizeColumnsToContents()
撑破视口（实测长文本 sum=1462）——窄窗出横向滚动条、最大化右侧大片空白。
真实 QSS 下量测（#117 教训：几何探针不得 stub set_style）。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QTableWidgetItem

_app: QApplication | None = None


def _ensure_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


@pytest.fixture()
def app():
    return _ensure_app()


@pytest.fixture()
def win(app, monkeypatch, tmp_path):
    from mdcx.consts import MAIN_PATH
    from mdcx.controllers.main_window import main_window as mw_mod
    from mdcx.controllers.main_window import style as style_mod

    monkeypatch.setattr(mw_mod, "run_startup_health_checks", lambda: None)
    monkeypatch.setattr(mw_mod, "show_netstatus", lambda: None)
    monkeypatch.setattr(mw_mod, "check_version", lambda: None)
    monkeypatch.setattr(mw_mod, "save_remain_list", lambda: None)
    monkeypatch.setattr(mw_mod, "apply_site_priority_theme", lambda _window: None)
    monkeypatch.setattr(style_mod.resources, "qtr", lambda p: str(MAIN_PATH / "resources" / p))
    monkeypatch.chdir(tmp_path)

    window = mw_mod.MyMAinWindow()
    window.set_style()  # 真实 QSS：字体度量影响列宽临界点
    for timer_name in ("timer", "timer_scrape", "timer_update", "timer_remain_task"):
        getattr(window, timer_name).stop()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def _goto_tool(win, app):
    for i in range(win.Ui.stackedWidget.count()):
        if win.Ui.stackedWidget.widget(i).objectName() == "page_tool":
            win.Ui.stackedWidget.setCurrentIndex(i)
            app.processEvents()
            return
    raise AssertionError("page_tool not found")


def _fill_long_rows(win, rows: int = 5) -> None:
    """按真实最坏形态填行：文件名/最后错误长文本（Stretch 列），其余列有界。"""
    tw = win.Ui.tableWidget_scrape_cache_failed
    tw.setRowCount(rows)
    for r in range(rows):
        tw.setItem(r, 0, QTableWidgetItem("非常长的文件名标题" * 20 + ".mp4"))
        tw.setItem(r, 1, QTableWidgetItem("ABCD-123"))
        tw.setItem(r, 2, QTableWidgetItem("3"))
        tw.setItem(r, 3, QTableWidgetItem("错误信息很长" * 20))
        tw.setItem(r, 4, QTableWidgetItem("2026-09-20 07:40:37"))
    app_event = QApplication.instance()
    assert app_event is not None
    app_event.processEvents()


def test_no_hscrollbar_at_user_window_sizes(win, app):
    """设计宽与用户截图宽（1032×737）下均不得出现横向滚动条。"""
    _goto_tool(win, app)
    tw = win.Ui.tableWidget_scrape_cache_failed
    for w, h in ((820, 692), (1032, 737)):
        win.resize(w, h)
        app.processEvents()
        total = sum(tw.columnWidth(c) for c in range(5))
        assert total <= tw.viewport().width(), f"win={w} 列总宽 {total} 超出视口 {tw.viewport().width()}"
        assert not tw.horizontalScrollBar().isVisible(), f"win={w} 仍显示横向滚动条"


def test_columns_fill_viewport_when_maximized(win, app):
    """宽窗下两列 Stretch 分摊剩余宽，列总宽须铺满视口（用户诉求 2）。"""
    _goto_tool(win, app)
    win.resize(1920, 1080)
    app.processEvents()
    tw = win.Ui.tableWidget_scrape_cache_failed
    total = sum(tw.columnWidth(c) for c in range(5))
    vp = tw.viewport().width()
    assert tw.width() > 650, f"表格未随窗口拉宽: {tw.width()}"
    assert total >= vp - 2, f"列总宽 {total} 未铺满视口 {vp}（右侧留白）"


def test_long_text_refresh_keeps_no_scrollbar(win, app):
    """长文本填充（等价刷新统计后）仍不得撑出横向滚动条。"""
    _goto_tool(win, app)
    win.resize(1032, 737)
    app.processEvents()
    _fill_long_rows(win)
    tw = win.Ui.tableWidget_scrape_cache_failed
    assert not tw.horizontalScrollBar().isVisible(), "长文本撑破视口，横向滚动条复现"
    assert tw.horizontalScrollBar().maximum() == 0, "滚动范围应为 0（列总宽不超视口）"
