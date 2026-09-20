"""议题 #154/#166 回归：编辑 NFO 覆盖层几何与切番号。

#154：内容区行式布局、字段随宽度拉伸、按钮钉底。
#166：覆盖层改为主页伴侣面板——右缘收到缩略图/结果树之间，不盖番号树；
保存/关闭成对居中；切番号时面板保持打开并刷新表单；未保存改动需确认。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox, QTreeWidgetItem

_app: QApplication | None = None


def _ensure_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


@pytest.fixture(scope="module")
def app():
    return _ensure_app()


@pytest.fixture()
def win(app, monkeypatch, tmp_path):
    from mdcx.controllers.main_window import main_window as mw_mod

    monkeypatch.setattr(mw_mod, "run_startup_health_checks", lambda: None)
    monkeypatch.setattr(mw_mod, "show_netstatus", lambda: None)
    monkeypatch.setattr(mw_mod, "check_version", lambda: None)
    monkeypatch.setattr(mw_mod, "save_remain_list", lambda: None)
    monkeypatch.setattr(mw_mod, "apply_site_priority_theme", lambda _window: None)
    monkeypatch.setattr(mw_mod.MyMAinWindow, "set_style", lambda self: None)
    monkeypatch.chdir(tmp_path)

    window = mw_mod.MyMAinWindow()
    from PyQt6.QtCore import QTimer

    for timer in window.findChildren(QTimer):
        timer.stop()
    window.show()
    yield window
    window.close()


def _open_overlay(win):
    win.Ui.widget_nfo.show()
    win._sync_nfo_overlay_geometry()


def _pair_geometry(nfo_w: int) -> tuple[int, int]:
    btn_w, gap = 91, 24
    pair_w = btn_w + gap + btn_w
    save_x = max((nfo_w - pair_w) // 2, 0)
    return save_x, save_x + btn_w + gap


def _add_result(win, name: str, number: str, title: str):
    from mdcx.models.model_types import ShowData

    data = ShowData.empty()
    data.show_name = name
    data.data.number = number
    data.data.title = title
    data.data.actors = ["演员A"]
    win.json_array[name] = data
    return QTreeWidgetItem(win.item_succ, [name])


def test_overlay_fields_widen_with_window(win):
    ui = win.Ui
    _open_overlay(win)
    content = ui.scrollAreaWidgetContents_nfo_editor
    assert content.layout() is not None, "覆盖层内容区未建立自适应布局"

    win.resize(1032, 737)
    win._sync_nfo_overlay_geometry()
    small_actor_w = ui.lineEdit_nfo_actor.width()
    small_content_w = content.width()

    win.resize(1920, 1080)
    win._sync_nfo_overlay_geometry()
    assert ui.lineEdit_nfo_actor.width() > small_actor_w, "最大化后字段未随宽度拉伸"
    assert content.width() > small_content_w, "内容区宽度未随视口增大"
    assert ui.lineEdit_nfo_actor.width() == pytest.approx(content.width() - 9 - 9 - 82 - 10, abs=4), (
        "单行字段应铺满内容区可用宽度"
    )


def test_overlay_pair_rows_do_not_overlap(win):
    ui = win.Ui
    _open_overlay(win)
    win.resize(1920, 1080)
    win._sync_nfo_overlay_geometry()
    pairs = (
        (ui.lineEdit_nfo_release, ui.lineEdit_nfo_runtime),
        (ui.lineEdit_nfo_score, ui.lineEdit_nfo_wanted),
        (ui.lineEdit_nfo_director, ui.lineEdit_nfo_series),
        (ui.lineEdit_nfo_studio, ui.lineEdit_nfo_publisher),
    )
    for left, right in pairs:
        assert left.x() + left.width() <= right.x(), f"{left.objectName()} 与 {right.objectName()} 重叠"
        assert right.x() + right.width() <= ui.scrollAreaWidgetContents_nfo_editor.width(), "右字段越界"


def test_overlay_buttons_centered_and_pinned(win):
    ui = win.Ui
    _open_overlay(win)
    nfo = ui.widget_nfo
    scroll = ui.scrollArea_nfo
    for width, height in ((1032, 737), (1920, 1080)):
        win.resize(width, height)
        win._sync_nfo_overlay_geometry()
        save, close = ui.pushButton_nfo_save, ui.pushButton_nfo_close
        expect_save, expect_close = _pair_geometry(nfo.width())
        assert save.y() == nfo.height() - 12 - 40, f"{width}x{height}: 保存按钮未钉底 (y={save.y()})"
        assert save.x() == expect_save, f"{width}x{height}: 保存按钮未成对居中 (x={save.x()})"
        assert close.x() == expect_close, f"{width}x{height}: 关闭按钮未成对居中 (x={close.x()})"
        assert close.x() - (save.x() + save.width()) == 24, "保存/关闭间距应为 24px"
        assert scroll.y() + scroll.height() <= save.y(), "滚动区覆盖了底部操作条"
        assert save.x() + save.width() <= close.x(), "保存/关闭按钮重叠"


def test_overlay_does_not_cover_result_tree(win):
    ui = win.Ui
    _open_overlay(win)
    for width, height in ((1032, 737), (1920, 1080)):
        win.resize(width, height)
        win._sync_nfo_overlay_geometry()
        nfo = ui.widget_nfo
        tree_left = ui.stackedWidget.x() + ui.treeWidget_number.x()
        assert nfo.x() + nfo.width() <= tree_left, (
            f"{width}x{height}: 覆盖层盖住番号树 nfo_right={nfo.x() + nfo.width()} tree_left={tree_left}"
        )
        thumb_right = ui.stackedWidget.x() + ui.label_thumb.x() + ui.label_thumb.width()
        assert nfo.x() + nfo.width() <= thumb_right + 1, (
            f"{width}x{height}: 覆盖层超出缩略图右缘 nfo_right={nfo.x() + nfo.width()} thumb_right={thumb_right}"
        )


def test_overlay_title_centered_and_comma_hints_moved(win):
    ui = win.Ui
    _open_overlay(win)
    win.resize(1032, 737)
    win._sync_nfo_overlay_geometry()
    assert ui.label_4.x() == 0
    assert ui.label_4.width() == ui.widget_nfo.width()
    assert ui.label_370.isHidden()
    assert ui.label_379.isHidden()
    assert ui.lineEdit_nfo_actor.placeholderText() == "多个以逗号隔开"
    assert ui.textEdit_nfo_tag.placeholderText() == "多个以逗号隔开"


def test_overlay_syncs_on_first_open_without_resize(win, monkeypatch):
    ui = win.Ui
    monkeypatch.setattr(win, "_check_main_file_path", lambda: True)
    monkeypatch.setattr(win, "_show_nfo_info", lambda: None)
    assert ui.widget_nfo.isHidden()
    win.main_open_nfo_click()
    nfo = ui.widget_nfo
    assert not nfo.isHidden()
    assert ui.pushButton_nfo_save.y() == nfo.height() - 12 - 40, "首次打开未同步钉底几何"
    expect_save, _ = _pair_geometry(nfo.width())
    assert ui.pushButton_nfo_save.x() == expect_save, "首次打开按钮未成对居中"
    assert ui.scrollAreaWidgetContents_nfo_editor.layout() is not None, "首次打开未建立内容布局"


def _select_result(win, item):
    tree = win.Ui.treeWidget_number
    tree.clearSelection()
    item.setSelected(True)
    QApplication.processEvents()


def test_switching_number_keeps_overlay_and_refreshes(win):
    ui = win.Ui
    first = _add_result(win, "1-1.ABP-622", "ABP-622", "标题622")
    second = _add_result(win, "1-2.ABP-608", "ABP-608", "标题608")
    _select_result(win, first)
    _open_overlay(win)
    assert ui.lineEdit_nfo_number.text() == "ABP-622"

    _select_result(win, second)
    assert not ui.widget_nfo.isHidden(), "切番号后覆盖层被收起"
    assert ui.lineEdit_nfo_number.text() == "ABP-608"
    assert ui.lineEdit_nfo_title.text() == "标题608"


def test_dirty_switch_cancel_keeps_current_number(win, monkeypatch):
    ui = win.Ui
    first = _add_result(win, "1-1.ABP-622", "ABP-622", "标题622")
    second = _add_result(win, "1-2.ABP-608", "ABP-608", "标题608")
    _select_result(win, first)
    _open_overlay(win)
    ui.lineEdit_nfo_title.setText("改过的标题")
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    _select_result(win, second)
    assert win.show_name == first.text(0)
    assert ui.lineEdit_nfo_title.text() == "改过的标题"
    assert not ui.widget_nfo.isHidden()
    selected = [item.text(0) for item in ui.treeWidget_number.selectedItems()]
    assert selected == [first.text(0)]


def test_page_change_hides_overlay(win):
    """议题 #177: 切页(非主界面)时编辑 NFO 面板暂隐, 切回主界面自动恢复。"""
    ui = win.Ui
    _open_overlay(win)
    assert not ui.widget_nfo.isHidden()
    ui.stackedWidget.setCurrentIndex(1)
    assert ui.widget_nfo.isHidden(), "切到日志页面板未暂隐"
    assert ui.stackedWidget.currentIndex() == 1
    ui.stackedWidget.setCurrentIndex(0)
    assert not ui.widget_nfo.isHidden(), "切回主界面面板未恢复"


def test_page_change_dirty_cancel_keeps_panel(win, monkeypatch):
    """议题 #177: 未保存改动时切页被取消, 面板保持打开留在主界面。"""
    ui = win.Ui
    first = _add_result(win, "1-1.ABP-622", "ABP-622", "标题622")
    _select_result(win, first)
    _open_overlay(win)
    ui.lineEdit_nfo_title.setText("改过的标题")
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)

    ui.stackedWidget.setCurrentIndex(1)
    assert ui.stackedWidget.currentIndex() == 0, "未保存时切页未被取消"
    assert not ui.widget_nfo.isHidden(), "取消切页后面板应保持打开"


def test_page_change_dirty_save_proceeds(win, monkeypatch):
    """议题 #177: 未保存改动时选保存则允许切页, 面板随切页暂隐。"""
    ui = win.Ui
    first = _add_result(win, "1-1.ABP-622", "ABP-622", "标题622")
    _select_result(win, first)
    _open_overlay(win)
    monkeypatch.setattr(win, "save_nfo_info", lambda: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Save)
    ui.lineEdit_nfo_title.setText("改过的标题")

    ui.stackedWidget.setCurrentIndex(1)
    assert ui.stackedWidget.currentIndex() == 1, "选保存后切页未被执行"
    assert ui.widget_nfo.isHidden(), "选保存切页后面板应暂隐"
