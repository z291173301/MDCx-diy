"""窗口状态矩阵回归测试：边框模式 x 窗口尺寸 x 日志展开收起 x 页面切换 x 最大化内容跟随。

回归背景（议题 #68）：QStackedWidget 只会把当前可见页 resize 到自身尺寸，
休眠页永远停留在设计尺寸 820x692。修复前 `_sync_page_layouts` 以 page.width()
为基准计算内部几何，"先缩放窗口再切页"时休眠页全部按陈旧尺寸布局——
日志页上栏只剩 480*0.61≈292 高、按钮飘出页面、工具页右侧被裁。

另修复：show_hide_logs 硬编码 resize(790, 418/689) 覆盖同步结果；
日志页/net 页按钮未跟随页面宽度；下栏隐藏时上栏仍只占 61%。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

import pytest
from PyQt6.QtWidgets import QApplication

_app: QApplication | None = None


def _ensure_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


@pytest.fixture(scope="module")
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
    # Geometry tests do not need the full QSS/resource loading path.
    monkeypatch.setattr(mw_mod.MyMAinWindow, "set_style", lambda self: None)
    monkeypatch.setattr(mw_mod, "apply_site_priority_theme", lambda _window: None)
    monkeypatch.setattr(
        style_mod.resources,
        "qtr",
        lambda relative_path: str(MAIN_PATH / "resources" / relative_path),
    )
    monkeypatch.chdir(tmp_path)

    window = mw_mod.MyMAinWindow()
    # 窗口构造后立即停表：几何测试不依赖定时器回调，
    # 保留运行中的 QTimer 会让 processEvents 触发网络/日志等无关副作用。
    for timer_name in ("timer", "timer_scrape", "timer_update", "timer_remain_task"):
        getattr(window, timer_name).stop()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def _goto(win, app, page_name):
    for i in range(win.Ui.stackedWidget.count()):
        if win.Ui.stackedWidget.widget(i).objectName() == page_name:
            win.Ui.stackedWidget.setCurrentIndex(i)
            app.processEvents()
            return win.Ui.stackedWidget.widget(i)
    raise AssertionError(f"page not found: {page_name}")


def test_dormant_pages_resize_with_window(win, app):
    """核心回归：缩放窗口后所有休眠页必须获得新尺寸，而非停留在设计尺寸。"""
    win.resize(1032, 737)
    app.processEvents()
    stacked = win.Ui.stackedWidget
    for i in range(stacked.count()):
        page = stacked.widget(i)
        assert page.width() == stacked.width(), f"{page.objectName()} 未跟随 stackedWidget 宽度"
        assert page.height() == stacked.height(), f"{page.objectName()} 未跟随 stackedWidget 高度"


def test_resize_first_then_switch_log_page_layout(win, app):
    """先缩放再切日志页（报告人操作序列）：上栏 61%、下栏 39%、按钮右缘锚定。"""
    win.resize(1032, 737)
    app.processEvents()
    page = _goto(win, app, "page_log")
    upper = win.Ui.textBrowser_log_main
    lower = win.Ui.textBrowser_log_main_2
    assert upper.height() == pytest.approx(page.height() * 0.61, abs=2)
    assert lower.isVisibleTo(page)
    assert lower.height() == pytest.approx(page.height() - upper.height() - 1, abs=2)
    assert lower.y() == upper.height() + 1
    # 按钮右缘距页面右缘约 22px（设计 822-800），且不出界
    btn = win.Ui.pushButton_start_cap2
    assert btn.geometry().right() <= page.width()
    assert page.width() - btn.geometry().right() <= 30


def test_log_lower_hidden_upper_fills_page(win, app):
    """收起下栏后上栏应铺满整页（而非仍占 61%），展开后恢复分栏。"""
    win.resize(1200, 900)
    app.processEvents()
    page = _goto(win, app, "page_log")
    upper = win.Ui.textBrowser_log_main

    win.show_hide_logs(False)
    app.processEvents()
    assert win.Ui.textBrowser_log_main_2.isHidden()
    assert upper.height() == pytest.approx(page.height(), abs=2)

    win.show_hide_logs(True)
    app.processEvents()
    assert win.Ui.textBrowser_log_main_2.isVisibleTo(page)
    assert upper.height() == pytest.approx(page.height() * 0.61, abs=2)


def test_nav_gap_uniform_when_entries_hidden(win, app, monkeypatch):
    """议题 #74：原生边框下开启隐藏入口后，剩余导航按钮间隙必须一致且等于 spacing。

    根因：导航 layout 的固定高度容器没有底部 Expanding spacer，隐藏入口后多余
    空间被摊进可见按钮间隙（实测 8→22px）。修复：垂直布局末尾加 Expanding spacer
    吸收多余空间。本测试锁定隐藏后的间隙与整体结构。
    """
    from mdcx.config.enums import Switch
    from mdcx.controllers.main_window import main_window as mw_mod

    old = list(mw_mod.manager.config.switch_on)
    monkeypatch.setattr(mw_mod.manager.config, "window_title", "show")  # 原生边框
    monkeypatch.setattr(mw_mod.manager.config, "switch_on", [*old, Switch.HIDE_ACTOR_NAV, Switch.HIDE_NFO_NAV])
    win.load_config()
    win._windows_auto_adjust()
    win.show()
    app.processEvents()

    layout = win.Ui.verticalLayout
    assert win.Ui.pushButton_emby_manager_nav.isHidden()
    assert win.Ui.pushButton_nfo_library.isHidden()

    nav_buttons = [
        win.Ui.pushButton_main,
        win.Ui.pushButton_log,
        win.Ui.pushButton_tool,
        win.Ui.pushButton_emby_manager_nav,
        win.Ui.pushButton_nfo_library,
        win.Ui.pushButton_setting,
        win.Ui.pushButton_net,
        win.Ui.pushButton_about,
    ]
    visible = [b for b in nav_buttons if not b.isHidden()]
    assert len(visible) == 6

    from itertools import pairwise

    gaps = [b.y() - (a.y() + a.height()) for a, b in pairwise(visible)]
    assert all(g == layout.spacing() for g in gaps), f"导航间隙不等于 spacing: {gaps}"


def test_maximize_button_present(win):
    """议题 #69: 最大化按钮恢复（#67 曾按报告人要求用 WindowMaximizeButtonHint 屏蔽）。

    #62/#66/#68 的最大化布局错乱根因已修复（见本文件其余用例），
    禁用按钮只是绕过症状且与拖拽边缘缩放能力自相矛盾，应恢复按钮。
    """
    from PyQt6.QtCore import Qt

    assert win.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint


def test_maximized_window_layout_sync(win, app):
    """最大化路径整体回归：showMaximized 后所有休眠页与内部组件跟随新窗口尺寸。"""
    win.showMaximized()
    app.processEvents()
    stacked = win.Ui.stackedWidget
    for i in range(stacked.count()):
        page = stacked.widget(i)
        assert page.width() == stacked.width(), f"{page.objectName()} 最大化后未跟随宽度"
        assert page.height() == stacked.height(), f"{page.objectName()} 最大化后未跟随高度"
    page = _goto(win, app, "page_log")
    upper = win.Ui.textBrowser_log_main
    assert upper.height() == pytest.approx(page.height() * 0.61, abs=2)
    assert win.Ui.pushButton_start_cap2.geometry().right() <= page.width()


def test_nav_buttons_hide_switch(win, app):
    """议题 #71: 设置-高级「隐藏入口」开关控制导航按钮显隐（保存后 load_config 即时生效）。"""
    from mdcx.config.enums import Switch
    from mdcx.config.manager import manager

    actor_btn = win.Ui.pushButton_emby_manager_nav
    nfo_btn = win.Ui.pushButton_nfo_library
    assert not actor_btn.isHidden()
    assert not nfo_btn.isHidden()

    old_switch_on = list(manager.config.switch_on)
    try:
        manager.config.switch_on = [*old_switch_on, Switch.HIDE_ACTOR_NAV, Switch.HIDE_NFO_NAV]
        win.load_config()
        app.processEvents()
        assert actor_btn.isHidden()
        assert nfo_btn.isHidden()
        # 设置页复选框同步回写勾选状态
        assert win.Ui.checkBox_hide_actor_nav.isChecked()
        assert win.Ui.checkBox_hide_nfo_nav.isChecked()

        # 取消开关后入口恢复显示
        manager.config.switch_on = old_switch_on
        win.load_config()
        app.processEvents()
        assert not actor_btn.isHidden()
        assert not nfo_btn.isHidden()
        assert not win.Ui.checkBox_hide_actor_nav.isChecked()
    finally:
        manager.config.switch_on = old_switch_on


def test_setting_tabs_scrollareas_follow_window(win, app):
    """设置页 12 个 tab 的 scrollArea 跟随窗口（#66 回归，含休眠 tab）。"""
    win.resize(1400, 950)
    app.processEvents()
    _goto(win, app, "page_setting")
    tab_widget = win.Ui.tabWidget
    for i in range(tab_widget.count()):
        tab_page = tab_widget.widget(i)
        assert tab_page.width() == tab_widget.width(), f"tab{i} 页未跟随 tabWidget"
    from mdcx.views.CustomClass import CustomScrollArea

    for i in range(tab_widget.count()):
        tab_page = tab_widget.widget(i)
        scroll = tab_page.findChild(CustomScrollArea)
        if scroll is not None and scroll.parentWidget() == tab_page:
            assert scroll.width() == pytest.approx(tab_widget.width() - 4, abs=2), f"tab{i} scrollArea 宽未同步"
            assert scroll.height() == pytest.approx(tab_widget.height() - 24, abs=2), f"tab{i} scrollArea 高未同步"


@pytest.mark.parametrize("border", ["show", "hide"])
def test_layout_correct_under_both_border_modes(win, app, border):
    """原生边框与隐藏边框两种外观下，日志页几何规则一致（报告人未开隐藏边框）。"""
    from mdcx.controllers.main_window import main_window as mw_mod

    mw_mod.manager.config.window_title = border
    win._windows_auto_adjust()
    app.processEvents()
    win.resize(1032, 737)
    app.processEvents()
    page = _goto(win, app, "page_log")
    upper = win.Ui.textBrowser_log_main
    assert upper.height() == pytest.approx(page.height() * 0.61, abs=2)
    assert win.Ui.pushButton_start_cap2.geometry().right() <= page.width()
    mw_mod.manager.config.window_title = "show"
    win._windows_auto_adjust()
    app.processEvents()


def _size(obj):
    return (obj.width(), obj.height())


def test_save_load_config_keeps_minimized_main_window(win, app, monkeypatch):
    """议题 #82：主窗最小化时，后台触发的配置保存/加载不得还原主窗。

    根因：save_config/load_config 末尾无条件
    `setWindowState(去最小化 | WindowActive)` + `activateWindow()`——Windows 原生
    边框下会强制还原最小化主窗。用户场景：主窗最小化跑刮削/操作演员管理器期间，
    任意自动保存把主窗弹出。修复：仅在主窗可见且未最小化时才恢复激活。
    """
    from mdcx.controllers.main_window import main_window as mw_mod

    # dummy manager 无 save 桩——本测试只验证窗口状态行为，配置落盘打桩跳过
    monkeypatch.setattr(mw_mod.manager, "save", lambda *a, **k: None, raising=False)

    win.show()
    app.processEvents()
    win.showMinimized()
    app.processEvents()
    assert win.isMinimized()

    win.save_config()
    app.processEvents()
    assert win.isMinimized(), "save_config 不应还原最小化主窗"

    win.load_config()
    app.processEvents()
    assert win.isMinimized(), "load_config 不应还原最小化主窗"


def test_maximize_content_follow_all_pages(win, app):
    """横向放大复现：窗口从设计尺寸放大到 1920 宽（等价 Windows 原生最大化）。"""

    from mdcx.views.CustomClass import CustomScrollArea

    # 1. 正常显示（设计尺寸）
    win.resize(1040, 760)
    win.show()
    app.processEvents()

    tool_scroll = win.Ui.page_tool.findChild(CustomScrollArea)
    setting_tab = win.Ui.tabWidget
    before_tool = _size(tool_scroll)
    before_setting = _size(setting_tab)
    before_main_page = _size(win.Ui.page_main)
    before_tree = _size(win.Ui.treeWidget_number)
    before_file_path = _size(win.Ui.label_file_path)

    # 2. 放大到 1920x1040（Windows 真机最大化等效）
    win.resize(1920, 1040)
    app.processEvents()

    print(f"window     : {win.width()}x{win.height()}")
    print(f"stacked    : {win.Ui.stackedWidget.width()}x{win.Ui.stackedWidget.height()}")
    print(f"page_main  : {before_main_page} -> {_size(win.Ui.page_main)}")
    print(f"tool_scroll: {before_tool} -> {_size(tool_scroll)}")
    print(f"setting_tab: {before_setting} -> {_size(setting_tab)}")
    print(f"main_tree  : {before_tree} -> {_size(win.Ui.treeWidget_number)}")
    print(f"file_path  : {before_file_path} -> {_size(win.Ui.label_file_path)}")

    # 页面本身跟随
    assert win.Ui.page_main.width() == win.Ui.stackedWidget.width(), "page_main 未跟随 stackedWidget"
    assert win.Ui.page_tool.width() == win.Ui.stackedWidget.width(), "page_tool 未跟随 stackedWidget"
    assert win.Ui.page_setting.width() == win.Ui.stackedWidget.width(), "page_setting 未跟随 stackedWidget"

    # 软件界面（初始可见页）：内部内容横向跟随
    assert win.Ui.treeWidget_number.geometry().right() == pytest.approx(win.Ui.page_main.width() - 18, abs=6), (
        f"软件界面结果树未锚定右缘: right={win.Ui.treeWidget_number.geometry().right()}"
    )
    assert win.Ui.label_file_path.width() == pytest.approx(win.Ui.page_main.width() - 34, abs=6), (
        f"软件界面文件路径标签未拉伸: {win.Ui.label_file_path.width()}"
    )
    # 议题 #173: 结果树宽随 cover_scale 拉伸贴向缩略图右缘, 最大化时左缘到缩略图右缘
    # 的 gap 应收窄(不再像固定 202 宽那样离缩略图 280px), 右缘仍贴页面右 18px。
    tree = win.Ui.treeWidget_number
    cover_scale = win.Ui.page_main.width() / 820
    thumb_right = int(580 * cover_scale)
    tree_gap_l = tree.x() - thumb_right
    assert tree_gap_l <= 80, f"结果树左缘到缩略图右缘 gap 未收窄: {tree_gap_l}px"
    # 工具/设置页为休眠页：容器几何即时跟随即可（content 拉伸在切页 show 时验证，
    # 见 test_switch_to_pages_after_maximize_content_visible）
    assert tool_scroll.width() == pytest.approx(win.Ui.page_tool.width() - 40, abs=4), (
        f"工具页 scrollArea 宽未跟随: {tool_scroll.width()} != {win.Ui.page_tool.width() - 40}"
    )
    assert setting_tab.width() == pytest.approx(win.Ui.page_setting.width() - 40, abs=4), (
        f"设置页 tabWidget 宽未跟随: {setting_tab.width()} != {win.Ui.page_setting.width() - 40}"
    )


def test_setting_config_bar_docked_to_bottom(win, app):
    """设置页底部配置操作浮框（当前配置/另存为/恢复默认/保存）跟随贴底 + 保存按钮右缘锚定."""

    win.resize(1040, 760)
    win.show()
    app.processEvents()

    setting_page = _goto(win, app, "page_setting")
    page_h = setting_page.height()

    win.resize(1920, 1040)
    app.processEvents()
    page_h = setting_page.height()

    # 整组控件贴新底部（设计基线距底 692-630=62，允许 DPI 误差）
    for btn, name in (
        (win.Ui.pushButton_save_new_config, "另存为"),
        (win.Ui.pushButton_init_config, "恢复默认"),
        (win.Ui.pushButton_save_config, "保存"),
    ):
        dock = page_h - btn.y()
        assert 55 <= dock <= 75, f"{name} 未贴底: 距底 {dock}（页高 {page_h}）"

    # 保存按钮右缘锚定（设计右距 820-731=89）
    right_gap = setting_page.width() - (win.Ui.pushButton_save_config.x() + win.Ui.pushButton_save_config.width())
    assert 80 <= right_gap <= 100, f"保存按钮右缘未锚定: 右距 {right_gap}"

    # 背景 label 拉伸贴宽
    assert win.Ui.label_config.width() >= setting_page.width() - 30, (
        f"配置浮框背景未拉伸: {win.Ui.label_config.width()} / {setting_page.width()}"
    )


def test_setting_form_inputs_stretch_with_viewport(win, app):
    """设置页表单输入控件随视口拉宽（gridLayout 重排，修复"表单缩在左侧"）."""

    win.resize(1040, 760)
    win.show()
    app.processEvents()

    _goto(win, app, "page_setting")
    # 切到刮削目录 tab（tab0），取其中行编辑框
    from PyQt6.QtWidgets import QLineEdit

    tab0 = win.Ui.tabWidget.widget(0)
    edits = tab0.findChildren(QLineEdit)
    assert edits, "刮削目录 tab 无输入框"
    before = max(e.width() for e in edits)

    win.resize(1920, 1040)
    app.processEvents()

    after = max(e.width() for e in edits)
    print(f"form edit width: {before} -> {after}")
    assert after > before + 100, f"表单输入框未随视口拉宽: {before} -> {after}"


def test_scroll_content_follow_viewport(win, app):
    """工具页 scrollArea 内部内容跟随视口宽（切页显示后验证，对齐用户观察路径）."""

    from mdcx.views.CustomClass import CustomScrollArea

    win.resize(1040, 760)
    win.show()
    app.processEvents()

    tool_page = _goto(win, app, "page_tool")
    tool_scroll = tool_page.findChild(CustomScrollArea)
    content = tool_scroll.widget()
    before_content_w = content.width()

    win.resize(1920, 1040)
    app.processEvents()

    # 视口放大后（页面保持可见），widgetResizable 应把内容拉到视口宽
    after_content_w = content.width()
    print(f"before: viewport={tool_scroll.width()} content={before_content_w}")
    print(f"after : viewport={tool_scroll.width()} content={after_content_w}")
    assert after_content_w > before_content_w, (
        f"工具页 scrollArea 内容宽未跟随视口: {before_content_w} -> {after_content_w}"
    )

    # 宽幅 groupBox 跟随拉伸（sync_wide_children_width，设计基准从 setWidget 登记取）
    design_w = getattr(content, "_wide_children_design_width", 0)
    extra = tool_scroll.viewport().width() - design_w
    from PyQt6.QtWidgets import QGroupBox

    wide_groups = [g for g in content.findChildren(QGroupBox) if g.parentWidget() is content and g.width() > 400]
    print(f"groupBox widths: {[(g.objectName(), g.width()) for g in wide_groups[:4]]}")
    for g in wide_groups:
        assert g.width() >= 700 + extra - 4, f"宽幅容器 {g.objectName()} 未跟随拉伸: {g.width()} (extra={extra})"


def test_probe_main_tool_content(win, app):
    """软件界面/软件工具页内容随视口拉宽（与设置页同款自适应）。"""

    from PyQt6.QtWidgets import QLineEdit

    win.resize(1040, 760)
    win.show()
    app.processEvents()

    tool_page = _goto(win, app, "page_tool")
    before_edit = max((e.width() for e in tool_page.findChildren(QLineEdit)), default=0)
    before_outline = win.Ui.label_outline.width()
    before_series_x = win.Ui.label_series.x()

    win.resize(1920, 1040)
    app.processEvents()

    # 软件工具页：输入框跟随拉宽
    after_edit = max((e.width() for e in tool_page.findChildren(QLineEdit)), default=0)
    print(f"tool_lineEdit : {before_edit} -> {after_edit}")
    print(f"main_outline  : {before_outline} -> {win.Ui.label_outline.width()}")
    print(f"series_x      : {before_series_x} -> {win.Ui.label_series.x()}")
    assert after_edit > before_edit + 200, f"工具页输入框未随视口拉宽: {before_edit} -> {after_edit}"

    # 软件界面：#135 信息区保持设计左列（与「番号/标题/封面」对齐），按封面增高下移；
    # #141 下划线/值列按 ×scale 等比例加长，右列下划线延伸到缩略图右缘。
    cover_scale = win.Ui.page_main.width() / 820
    cover_bottom = int(160 + 220 * cover_scale)
    info_delta = cover_bottom - 380
    thumb_right = int(580 * cover_scale)
    tree_x = win.Ui.treeWidget_number.x()
    assert win.Ui.label_outline.x() == 70, "简介左缘应保持设计 x=70（与左上角对齐）"
    assert win.Ui.label_outline.x() + win.Ui.label_outline.width() == thumb_right, "简介下划线右缘应延伸到缩略图右缘"
    assert win.Ui.label_series.x() == int(350 * cover_scale), "右列应随 ×scale 右移"
    assert win.Ui.label_series.x() + win.Ui.label_series.width() == thumb_right, "右列下划线右缘应延伸到缩略图右缘"
    # 信息区首行下移到封面框下方（不被放大后的黑框盖住）
    assert win.Ui.label_outline.y() == pytest.approx(430 + info_delta, abs=2), "信息区未按封面增高下移"
    assert win.Ui.label_outline.y() >= cover_bottom, "信息区首行仍在封面框内（#135 未修复）"
    # 上区受右侧按钮限制的拉伸右界
    assert win.Ui.label_number.geometry().right() == pytest.approx(min(450, tree_x - 30), abs=4)

    # 幂等性：还原-再放大后，几何与直接放大结果一致（固定公式基准，无累积漂移）
    win.resize(1040, 760)
    app.processEvents()
    win.resize(1920, 1040)
    app.processEvents()
    # #135/#141 固定公式：左列 x 恒为设计值，右列 x 与下划线右缘由 ×scale 决定
    assert win.Ui.label_series.x() == int(350 * cover_scale), "右列 x 漂移"
    assert win.Ui.label_outline.x() == 70, "简介 x 漂移"
    assert win.Ui.label_outline.x() + win.Ui.label_outline.width() == thumb_right, "简介右缘漂移"
    assert win.Ui.label_outline.y() == pytest.approx(430 + info_delta, abs=2), "信息区 y 漂移"
    assert after_edit == max((e.width() for e in tool_page.findChildren(QLineEdit)), default=0), "工具页输入框宽漂移"


def test_runtime_row_follows_right_column_on_maximize(win, app):
    """议题 #82：最大化后「时长」行（右列 y=530）必须随右列平移。

    根因：_sync_page_layouts 下半区右列平移清单漏了 label_22（时长：标签,
    设计 x=310）与 label_runtime（时长值, 设计 x=350）——系列/发行平移后，
    时长行滞留原位，与左列日期行重叠错位。
    """
    win.resize(1040, 760)
    win.show()
    app.processEvents()

    win.resize(1920, 1040)
    app.processEvents()

    # #141：右列随 ×scale 右移（label_22=310×scale，label_runtime=350×scale），
    # 时长行 y 与日期行（y=530）一致地下移。#154 撤销 #152 的行高增长：简介/标签
    # 恒定 40px，其下各行只随封面增高量 info_delta 下移，故 info_grow == info_delta。
    cover_scale = win.Ui.page_main.width() / 820
    info_delta = int(160 + 220 * cover_scale) - 380
    info_grow = info_delta
    assert win.Ui.label_22.x() == int(310 * cover_scale), f"时长标签 x 漂移: {win.Ui.label_22.x()}"
    assert win.Ui.label_runtime.x() == int(350 * cover_scale), f"时长值 x 漂移: {win.Ui.label_runtime.x()}"
    assert win.Ui.label_22.y() == pytest.approx(530 + info_grow, abs=2), (
        f"时长标签未随信息区下移: {win.Ui.label_22.y()} != {530 + info_grow}"
    )


def test_switch_to_pages_after_maximize_content_visible(win, app):
    """最大化后切到三个页面，内容尺寸正确（复现用户切页观察）."""

    win.resize(1040, 760)
    win.show()
    app.processEvents()
    win.resize(1920, 1040)
    app.processEvents()

    from mdcx.views.CustomClass import CustomScrollArea

    # 软件工具页
    tool_page = _goto(win, app, "page_tool")
    tool_scroll = tool_page.findChild(CustomScrollArea)
    assert tool_scroll.width() > 700, f"工具页内容未跟随: {tool_scroll.width()}"
    assert tool_scroll.width() == pytest.approx(tool_page.width() - 40, abs=4)
    tool_content = tool_scroll.widget()
    print(f"tool: viewport={tool_scroll.width()} content={tool_content.width()}")
    # 内容必须跟随视口拉宽（用户症状：内容停在 782/860 不放大）
    assert tool_content.width() >= tool_scroll.width() - 30, (
        f"工具页 scrollArea 内容未拉伸: content={tool_content.width()} viewport={tool_scroll.width()}"
    )

    # 软件设置页
    setting_page = _goto(win, app, "page_setting")
    assert win.Ui.tabWidget.width() > 700, f"设置页 tabWidget 未跟随: {win.Ui.tabWidget.width()}"
    assert win.Ui.tabWidget.width() == pytest.approx(setting_page.width() - 40, abs=4)
    # 当前 tab 的 scrollArea 内容同样必须拉伸
    current_tab = win.Ui.tabWidget.currentWidget()
    tab_scroll = current_tab.findChild(CustomScrollArea)
    if tab_scroll is not None and tab_scroll.parentWidget() == current_tab:
        tab_content = tab_scroll.widget()
        print(f"set: viewport={tab_scroll.width()} content={tab_content.width()}")
        assert tab_content.width() >= tab_scroll.width() - 30, (
            f"设置页内容未拉伸: content={tab_content.width()} viewport={tab_scroll.width()}"
        )


def test_nfo_lib_layout_probe(win, app):
    win.resize(1040, 760)
    win.show()
    app.processEvents()

    page = _goto(win, app, "page_nfo_library")
    top = win.Ui.nfo_lib_top_bar
    content = win.Ui.nfo_lib_content
    print(
        f"before: page={page.width()}x{page.height()} top={top.height()} content={content.width()}x{content.height()}"
    )

    win.resize(1920, 1040)
    app.processEvents()
    print(
        f"after : page={page.width()}x{page.height()} top={top.height()} content={content.width()}x{content.height()}"
    )
    print(
        f"gap right={page.width() - (content.x() + content.width())} bottom={page.height() - (content.y() + content.height())}"
    )

    # 嵌套子布局激活验证：page → content → scrollArea → formLayout
    form_scroll = win.Ui.scrollArea_nfo_lib_form
    form_content = win.Ui.scrollAreaWidgetContents_nfo_lib
    form_layout = form_content.layout()
    print(f"form scroll viewport={form_scroll.viewport().width()} content={form_content.width()}")
    if form_layout is not None:
        print(f"form layout active={form_layout.isEnabled()} activated={form_layout.isEmpty()}")

    # 断言内容宽度跟随视口（消除右侧 194px 空白）
    assert form_content.width() >= form_scroll.viewport().width() - 20, (
        f"表单内容未跟随视口: content={form_content.width()} viewport={form_scroll.viewport().width()}"
    )

    # 保存按钮可见性：最大化后表单内容压缩简介/标签面积（议题 #78 用户建议），
    # 保存按钮必须落在滚动视口内、无需滚动即可见
    save_btn = win.Ui.pushButton_nfo_lib_save
    print(f"save btn: y={save_btn.y()} h={save_btn.height()} visible={save_btn.isVisible()}")
    print(f"form content height={form_content.height()} viewport height={form_scroll.viewport().height()}")
    # 简介/标签面积压缩（60px 固定，消除下拉栏）
    outline_h = win.Ui.plainTextEdit_nfo_lib_outline.height()
    tag_h = win.Ui.plainTextEdit_nfo_lib_tag.height()
    print(f"outline h={outline_h} tag h={tag_h}")
    assert outline_h == 60, f"简介未压到 60: {outline_h}"
    assert tag_h == 60, f"标签未压到 60: {tag_h}"
    # 保存按钮必须在视口内（无滚动可见），留 4px 安全边距应对平台差异
    save_bottom = save_btn.y() + save_btn.height()
    assert save_bottom <= form_scroll.viewport().height() - 4, (
        f"保存按钮仍被推视口: bottom={save_bottom} viewport={form_scroll.viewport().height()}"
    )
    # 内容总高不显著超过视口（缩列下拉栏消除——用户报告"下拉栏"现象）
    assert form_content.height() <= form_scroll.viewport().height() + 40, (
        f"表单总高超视口: content={form_content.height()} viewport={form_scroll.viewport().height()}"
    )


def test_nfo_lib_form_compact_and_no_clip_when_small(win, app):
    """议题 #117：小窗时输入框右缘不被裁、无垂直滚动条、保存按钮免滚动可见。

    用户截图（1032x737，Windows）：表单列内容宽顶在 QFormLayout 首选宽 301，
    垂直滚动条一出现就占掉 14px 视口宽 → 内容右缘被裁 9px，输入框左侧圆角正常、
    右侧被裁成平口；同时「保存当前 NFO」在视口外，必须下拉才见。
    修复三点：
    1. layout 驱动内容的宽度下限改用布局硬最小值——视口窄于首选宽时内容跟随视口；
    2. 该页内容底部余量收紧为 8px（默认 72 是给设置页底部浮框带避让用的，
       信息管理页没有浮框，白占一行多高度凭空顶出滚动条）；
    3. 视口仍放不下整表时，按缺口压缩简介/标签两个多行框（60 → 最低 40）。
    """
    form_scroll = win.Ui.scrollArea_nfo_lib_form
    form_content = win.Ui.scrollAreaWidgetContents_nfo_lib
    outline = win.Ui.plainTextEdit_nfo_lib_outline
    tag = win.Ui.plainTextEdit_nfo_lib_tag
    save_btn = win.Ui.pushButton_nfo_lib_save

    def probe():
        app.processEvents()
        viewport = form_scroll.viewport()
        return {
            "clip": form_content.width() - viewport.width(),
            "scroll": form_scroll.verticalScrollBar().maximum(),
            "outline_h": outline.height(),
            "tag_h": tag.height(),
            "save_hidden": save_btn.y() + save_btn.height() > viewport.height(),
        }

    # 常规小窗：整表放得下 → 保持设计高度 60（最大化布局不变），无滚动条
    win.resize(1032, 737)
    win.show()
    _goto(win, app, "page_nfo_library")
    at_small = probe()
    assert at_small["clip"] <= 0, f"输入框右缘被视口裁剪: {at_small['clip']}px"
    assert at_small["scroll"] == 0, f"小窗仍出现垂直滚动条: range={at_small['scroll']}"
    assert not at_small["save_hidden"], "保存按钮被推出视口"
    assert at_small["outline_h"] == 60 and at_small["tag_h"] == 60, "放得下时简介/标签应保持设计高"

    # 更矮的窗口：压缩简介/标签换取免滚动可见，且不得出现横向裁剪
    win.resize(1032, 560)
    at_tiny = probe()
    assert at_tiny["clip"] <= 0, f"压缩后输入框右缘被裁剪: {at_tiny['clip']}px"
    assert at_tiny["scroll"] == 0, f"压缩后仍有垂直滚动条: range={at_tiny['scroll']}"
    assert not at_tiny["save_hidden"], "压缩后保存按钮仍被推出视口"
    assert at_tiny["outline_h"] < 60, f"视口放不下时简介未压缩: {at_tiny['outline_h']}"
    assert at_tiny["outline_h"] >= 40, f"压缩低于可读下限: {at_tiny['outline_h']}"
    assert at_tiny["outline_h"] == at_tiny["tag_h"], "简介/标签压缩幅度应一致"

    # 回到放大尺寸：必须自动恢复设计高（幂等，不依赖当前值）
    win.resize(1920, 1080)
    at_max = probe()
    assert at_max["outline_h"] == 60 and at_max["tag_h"] == 60, (
        f"最大化后简介/标签未恢复设计高: {at_max['outline_h']}/{at_max['tag_h']}"
    )
    assert at_max["clip"] <= 0, f"最大化后输入框右缘被裁剪: {at_max['clip']}px"


def test_scrollareas_restore_compact_after_maximize(win, app):
    """议题 #82：最大化→还原后，各页 scrollArea 内容几何必须回落紧凑基线。

    根因三层叠加：
    1. sync_wide_children_width 只增不减（extra<=0 直接 return），最大化拉宽的
       宽幅容器还原时不缩回；
    2. content.minimumWidth 从「拉宽后的」childrenRect 计算并锁死，widgetResizable
       受 minimumWidth 阻挡无法把内容缩回视口——设置/工具页内容右缘被裁剪；
    3. layout 驱动内容（NFO 表单 QFormLayout）的 minimumHeight 从膨胀
       childrenRect 计算：Expanding 行（简介/标签多行框）在超高容器中分得额外
       空间，childrenRect 抬高 → 最小高自锁（993 降不回紧凑 674），保存按钮
       被推出视口。
    修复：宽幅容器按「设计几何+extra」双向幂等伸缩；min_width 以设计宽为上界；
    layout 驱动内容的 min 尺寸改用 layout.sizeHint（紧凑排布，与容器拉伸无关）。
    """
    from mdcx.views.CustomClass import CustomScrollArea

    win.resize(1040, 760)
    win.show()
    app.processEvents()
    _goto(win, app, "page_nfo_library")

    # 最大化 → 还原
    win.resize(1920, 1040)
    app.processEvents()
    win.resize(1040, 760)
    app.processEvents()

    # 设置页当前 tab：内容宽度回落视口内，min 宽不再锁死在最大化值（1599）
    _goto(win, app, "page_setting")
    tab0 = win.Ui.tabWidget.widget(0)
    scroll = tab0.findChild(CustomScrollArea)
    assert scroll.widget().width() <= scroll.viewport().width() + 2, (
        f"设置页内容宽未回落: content={scroll.widget().width()} viewport={scroll.viewport().width()}"
    )
    assert scroll.widget().minimumWidth() <= 800, f"设置页内容 min 宽锁死: {scroll.widget().minimumWidth()}"

    # 工具页：同上（曾锁死 1603）
    _goto(win, app, "page_tool")
    tool_scroll = win.Ui.page_tool.findChild(CustomScrollArea)
    assert tool_scroll.widget().width() <= tool_scroll.viewport().width() + 2, (
        f"工具页内容宽未回落: content={tool_scroll.widget().width()} viewport={tool_scroll.viewport().width()}"
    )
    assert tool_scroll.widget().minimumWidth() <= 800, f"工具页内容 min 宽锁死: {tool_scroll.widget().minimumWidth()}"

    # NFO 表单：内容回落紧凑、保存按钮回到视口内（曾 y=946 > 视口 674）
    _goto(win, app, "page_nfo_library")
    app.processEvents()
    form_scroll = win.Ui.scrollArea_nfo_lib_form
    form_content = win.Ui.scrollAreaWidgetContents_nfo_lib
    assert form_content.height() <= 760, f"NFO 表单未回落紧凑: {form_content.height()}"
    save_btn = win.Ui.pushButton_nfo_lib_save
    assert save_btn.y() + save_btn.height() <= form_scroll.viewport().height() + 4, (
        f"NFO 保存按钮仍在视口外: bottom={save_btn.y() + save_btn.height()} viewport={form_scroll.viewport().height()}"
    )


def test_setting_all_tabs_wide_boxes_fill_viewport(win, app):
    """最大化后 12 个设置 tab 的全部宽幅顶层 groupBox 必须拉伸到位。

    回归背景：.ui 中 34 处 groupBox 带 maximumWidth=860 设计器遗留上限，
    sync_wide_children_width 的 setGeometry 被上限夹断——同一页面内部分
    groupBox 拉满（如刮削模式的"多线程刮削"）、部分停在 860（如"刮削模式"
    框），内容右侧大面积留白。刮削目录页无上限、拉伸正常（用户确认基准）。
    """
    from mdcx.views.CustomClass import CustomScrollArea

    _goto(win, app, "page_setting")
    win.resize(1920, 1170)
    win.show()
    app.processEvents()

    ui = win.Ui
    checked = 0
    for i in range(ui.tabWidget.count()):
        ui.tabWidget.setCurrentIndex(i)
        app.processEvents()
        page = ui.tabWidget.widget(i)
        area = page.findChild(CustomScrollArea)
        content = area.widget()
        registry = getattr(content, "_wide_children_design", None)
        if not registry:
            continue
        design_w = getattr(content, "_wide_children_design_width", 0)
        extra = area.viewport().width() - design_w
        for entry in registry:
            box = entry.widget
            _, _, design_box_w, _ = entry.geometry
            expected = design_box_w + extra
            assert box.width() == expected, (
                f"tab{i}({ui.tabWidget.tabText(i)}) {box.objectName()} 拉伸被夹断: "
                f"w={box.width()} 期望={expected}（maximumWidth={box.maximumWidth()}）"
            )
            checked += 1
    assert checked >= 30, f"宽幅容器登记异常地少: {checked}"


def test_setting_config_bar_all_children_docked(win, app):
    """浮框组全部子件（含「当前配置:」label_241）必须位于底部浮框带内。

    回归背景：_sync_page_layouts 浮框段漏同步 label_241，最大化后它停在
    设计位置 y=629，与下移到页底的浮框带脱离、悬在滚动内容中部。
    """
    _goto(win, app, "page_setting")
    win.resize(1920, 1170)
    win.show()
    app.processEvents()

    ui = win.Ui
    bar = ui.label_config
    for name in (
        "label_241",
        "comboBox_change_config",
        "pushButton_save_new_config",
        "pushButton_init_config",
        "pushButton_save_config",
    ):
        w = getattr(ui, name)
        assert bar.y() <= w.y() < bar.y() + bar.height(), (
            f"{name} 脱离浮框带: y={w.y()} 带范围=[{bar.y()},{bar.y() + bar.height()})"
        )


def test_setting_content_clears_config_bar_when_scrolled(win, app):
    """滚动到底时设置页末行必须位于浮框带上方。

    浮框带（label_config）盖住滚动视口底部 intrusion px；内容最小高必须
    含 ≥ intrusion 的底部余量，否则最后一行文字从浮框后透出（用户截图）。
    layout 驱动内容（NFO 表单）此前 sizeHint 不加余量同样受影响。
    """
    from mdcx.views.CustomClass import CustomScrollArea

    _goto(win, app, "page_setting")
    win.resize(1920, 1170)
    win.show()
    app.processEvents()

    ui = win.Ui
    intrusion = (
        ui.tabWidget.widget(0)
        .findChild(CustomScrollArea)
        .mapTo(ui.page_setting, ui.tabWidget.widget(0).findChild(CustomScrollArea).viewport().rect().bottomLeft())
        .y()
        - ui.label_config.y()
    )
    margin = CustomScrollArea._CONTENT_BOTTOM_MARGIN
    assert margin > intrusion, f"内容底部余量 {margin} 不足以避开浮框侵入 {intrusion}"

    # layout 驱动页（NFO）：min 高 = sizeHint + 余量
    for i in range(ui.tabWidget.count()):
        page = ui.tabWidget.widget(i)
        area = page.findChild(CustomScrollArea)
        content = area.widget()
        if content.layout() is None:
            continue
        ui.tabWidget.setCurrentIndex(i)
        app.processEvents()
        hint_h = content.layout().sizeHint().height()
        assert content.minimumHeight() == hint_h + margin, (
            f"tab{i}({ui.tabWidget.tabText(i)}) layout 内容 min 高缺底部余量: "
            f"{content.minimumHeight()} != {hint_h}+{margin}"
        )


def test_left_status_badges_follow_window_bottom(win, app):
    """议题 #86/#102：左侧状态区随窗口底边同步，并预留 40px 底距（#102 用户反馈贴底太靠下）。

    回归背景：label_show_version/label_local_number 固定在设计 y 坐标，
    窗口最大化后留在上半区，与侧栏贴底的「正常模式」字段分离，
    视觉上像状态条移位（用户图 3 红框标注「不正常应该下移」）。
    窗口 1920x1170 时 label_show_version 应移至 y≈929（1170-201-40），
    label_local_number 移至 y≈1109（1170-21-40）。
    """
    _goto(win, app, "page_main")
    win.resize(1920, 1170)
    win.show()
    app.processEvents()

    assert win.Ui.label_show_version.y() == 929, (
        f"label_show_version 未贴底预留 40px: y={win.Ui.label_show_version.y()}"
    )
    assert win.Ui.label_local_number.y() == 1109, (
        f"label_local_number 未贴底预留 40px: y={win.Ui.label_local_number.y()}"
    )


# ============ 议题 #102：四项 UI 交互模拟验证 ============


def test_minimized_main_not_popped_on_app_activate(win, app):
    """议题 #102-①：主窗最小化后，应用激活事件（Emby 演员管理器任意操作/切任务
    让 app 重新激活）不得把主窗弹出前台。

    回归背景：eventFilter 的 ApplicationActivate 分支对隐藏/最小化主窗无条件
    show()，点 Emby 对话框即触发、主窗被拉出（用户截图「任何操作都弹主窗」）。
    修法：最小化时维持状态不动；仅非最小化的隐藏态保留 show()。

    模拟方式：eventFilter 挂载在 textBrowser_log_main 的 viewport 上
    （init.py:224-225），用 QApplication.sendEvent 向该 viewport 投递
    ApplicationActivate 事件，驱动真实守卫路径。
    """
    from PyQt6.QtCore import QEvent

    win.show()
    app.processEvents()

    # 最小化主窗（模拟用户最小化后去操作 Emby 管理器）
    win.showMinimized()
    app.processEvents()
    assert win.isMinimized(), "前置失败：主窗未最小化"

    viewport = win.Ui.textBrowser_log_main.viewport()
    activate = QEvent(QEvent.Type.ApplicationActivate)
    app.sendEvent(viewport, activate)
    app.processEvents()

    # 守卫生效：最小化状态维持，未被 showNormal/弹出。
    # 注：Qt 语义下 isVisible() 在最小化态恒为 True（含 minimized），不能据此判"被弹出"；
    # 正确判据是 isMinimized() 仍为 True（show() 会把它转成非最小化的可见态并弹出）。
    assert win.isMinimized(), "最小化主窗被 ApplicationActivate 弹出（状态脱离 minimized）"


def test_hidden_non_minimized_main_not_shown_on_app_activate(win, app):
    """议题 #132：主窗隐藏（托盘图标隐藏 / 关闭到托盘 / 最小化到托盘）后，应用激活
    事件（操作 Emby 演员管理器等工具会触发）不得把隐藏的主窗弹出前台。

    回归背景：议题 #102 曾保留「非最小化隐藏态」的 show()（当时认为隐藏态需要被
    恢复），但 #132 实测反馈：仅用托盘图标隐藏主窗后，对演员管理器做任何操作仍会
    把主窗弹出；要先最小化再隐藏才不弹。根因即此分支对非最小化隐藏态调用 show()。
    修复：隐藏是用户主动行为，恢复只由托盘图标/菜单触发，ApplicationActivate 不再
    自动 show()。
    """
    from PyQt6.QtCore import QEvent

    win.show()
    app.processEvents()
    win.hide()
    app.processEvents()
    assert not win.isVisible() and not win.isMinimized(), "前置失败：主窗应为非最小化隐藏态"

    viewport = win.Ui.textBrowser_log_main.viewport()
    app.sendEvent(viewport, QEvent(QEvent.Type.ApplicationActivate))
    app.processEvents()

    assert not win.isVisible(), "隐藏主窗被 ApplicationActivate 自动弹出（议题 #132）"


def test_tray_hidden_main_stays_hidden_after_manager_operation(win, app, monkeypatch):
    """议题 #132 主场景：托盘隐藏主窗后打开演员管理器并触发应用激活，主窗保持隐藏。

    ApplicationActivate 由操作演员管理器触发，等价于向主窗 eventFilter 投递该事件。
    """
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QSystemTrayIcon

    from mdcx.controllers.main_window import main_window as mw_mod
    from mdcx.controllers.main_window.tool_handlers import (
        pushButton_emby_actor_manager_clicked,
    )

    # tray_icon_click 仅在 Windows 走隐藏分支（IS_WINDOWS 门控），测试环境显式开启
    monkeypatch.setattr(mw_mod, "IS_WINDOWS", True)

    win.show()
    app.processEvents()

    # 用户点击托盘图标隐藏主窗（tray_icon_click 的 hide 路径）
    win.tray_icon_click(QSystemTrayIcon.ActivationReason.Trigger)
    app.processEvents()
    assert not win.isVisible(), "前置失败：托盘图标未隐藏主窗"

    # 打开演员管理器，并模拟其操作触发应用激活事件
    pushButton_emby_actor_manager_clicked(win)
    app.processEvents()
    viewport = win.Ui.textBrowser_log_main.viewport()
    app.sendEvent(viewport, QEvent(QEvent.Type.ApplicationActivate))
    app.processEvents()

    assert not win.isVisible(), "托盘隐藏后操作演员管理器把主窗弹出了（议题 #132）"


def test_tray_icon_click_restores_main_window_after_hide(win, app, monkeypatch):
    """议题 #132 配套：移除 ApplicationActivate 自动 show() 后，托盘图标点击仍能恢复主窗。

    防止修复过度——恢复显示必须继续由托盘交互负责。
    """
    from PyQt6.QtWidgets import QSystemTrayIcon

    from mdcx.controllers.main_window import main_window as mw_mod

    monkeypatch.setattr(mw_mod, "IS_WINDOWS", True)

    win.show()
    app.processEvents()
    win.tray_icon_click(QSystemTrayIcon.ActivationReason.Trigger)
    app.processEvents()
    assert not win.isVisible(), "前置失败：托盘图标未隐藏主窗"

    # 再次点击托盘图标 → 恢复显示
    win.tray_icon_click(QSystemTrayIcon.ActivationReason.Trigger)
    app.processEvents()
    assert win.isVisible(), "托盘图标点击未恢复主窗显示"


def test_main_page_cover_scales_proportionally_when_maximized(win, app):
    """议题 #102-②：主界面封面区（poster/thumb 图片框 + 尺寸文字）最大化后
    按设计基准宽 820 横向等比放大，宽高与 x 同 scale、y 不变（纵向位置保留）。

    回归背景：绝对定位布局未把封面区纳入横向同步，最大化后 4 个 label 停留
    设计 220px 高、停在页面顶部不随窗口放大（用户标注「图片区域及图片等比放大」）。
    """
    _goto(win, app, "page_main")
    win.resize(1920, 1080)
    win.show()
    app.processEvents()

    ui = win.Ui
    # stackedWidget 可用宽 = width - 210 - 2 = 1708；scale = 1708/820
    stacked_w = ui.stackedWidget.width()
    scale = stacked_w / 820
    assert stacked_w == 1708, f"前置：最大化 stackedWidget 宽 {stacked_w} ≠ 1708"

    assert ui.label_poster.width() == int(156 * scale), f"封面框宽未按 scale 放大: {ui.label_poster.width()}"
    assert ui.label_poster.height() == int(220 * scale), f"封面框高未按 scale 放大: {ui.label_poster.height()}"
    assert ui.label_poster.x() == int(80 * scale), f"封面框 x 未按 scale 平移: {ui.label_poster.x()}"
    assert ui.label_poster.y() == 160, f"封面框 y 应保留设计 160: {ui.label_poster.y()}"

    assert ui.label_thumb.width() == int(328 * scale), f"缩略框宽未按 scale 放大: {ui.label_thumb.width()}"
    assert ui.label_thumb.height() == int(220 * scale), f"缩略框高未按 scale 放大: {ui.label_thumb.height()}"
    assert ui.label_thumb.x() == int(252 * scale), f"缩略框 x 未按 scale 平移: {ui.label_thumb.x()}"

    assert ui.label_poster_size.width() == int(411 * scale), f"封面尺寸文字宽未按 scale: {ui.label_poster_size.width()}"
    assert ui.label_thumb_size.width() == int(201 * scale), f"缩略尺寸文字宽未按 scale: {ui.label_thumb_size.width()}"


def test_nfo_lib_info_page_no_right_blank_when_maximized(win, app):
    """议题 #102-④：信息管理页（NFO 库）最大化后表单区右侧无残留空白。

    回归背景：该现象在 v2.0.9 用户截图中标注「这不正常」，实为议题 #78 描述的
    「右侧残留 ~194px 空白」+ #82 还原锁死——v2.1.0 已由 _sync_page_layouts
    逐页重排 + CustomScrollArea min 宽回落覆盖。本测试锁定当前代码无回归：
    表单内容宽必须跟随视口（右侧不留 194px 空白）。
    """
    _goto(win, app, "page_nfo_library")
    win.resize(1920, 1080)
    win.show()
    app.processEvents()

    form_scroll = win.Ui.scrollArea_nfo_lib_form
    form_content = win.Ui.scrollAreaWidgetContents_nfo_lib
    right_gap = form_scroll.viewport().width() - form_content.width()
    assert right_gap <= 20, f"信息管理页右侧仍残留空白 {right_gap}px（应 ≤20）"
