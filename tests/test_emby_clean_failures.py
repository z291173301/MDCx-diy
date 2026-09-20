"""议题 #162 阶段1 回归: 数据清洗失败原因透传、逐条日志与完成引导。

背景: 清洗是直接改写服务器的(无"待同步"残留), 但旧实现把 clean_actor_data 生成的
失败原因字符串在 UI 回调处丢弃——用户只看到「失败: 75」计数, 无名字无原因无指引,
误以为还要点「开始全部更新同步」(无待同步项故"没反应")。

修复三层: ①actor_done 信号携带 msg; ②失败逐条落日志; ③完成弹窗列失败名单 +
「再点数据清洗重试」指引 + 「无需再点同步」说明。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.asyncio


def _make_actor(name: str, overview: str = "旧简介"):
    from mdcx.tools.emby_actor_manager import ActorInfo

    return ActorInfo(
        name=name,
        actor_id=f"id-{name}",
        server_id="srv",
        has_image=True,
        has_overview=bool(overview),
        existing_overview=overview,
    )


def _fake_dialog(actors):
    from mdcx.tools.emby_actor_manager_ui import EmbyActorManagerDialog

    logs: list[str] = []
    ns = SimpleNamespace(
        _actors=actors,
        _clean_items={a.actor_id: ("清洗后", False) for a in actors},
        _clean_failed=[],
        log=logs.append,
        progress_bar=SimpleNamespace(setVisible=lambda *_: None),
        _set_buttons_enabled=lambda *_: None,
        _set_status=lambda *_: None,
        _populate_table=lambda *_: None,
        _update_statistics=lambda *_: None,
        # 议题 #25: 真类新增会话守卫, fake self 无 sender 语义, 恒判"非陈旧"
        _is_stale_session=lambda: False,
    )
    return ns, logs, EmbyActorManagerDialog


def test_clean_failure_logged_with_reason_and_collected():
    """失败回调: 原因逐条进日志、进 _clean_failed; 内存原值保持(可重试)。"""
    actors = [_make_actor("失败者")]
    ns, logs, dialog_cls = _fake_dialog(actors)
    dialog_cls._on_clean_actor_done(ns, "id-失败者", False, "❌ 失败者 数据清洗失败: HTTP 400 bad date")
    assert any("HTTP 400 bad date" in line for line in logs), "失败原因必须落日志"
    assert ns._clean_failed == [("id-失败者", "❌ 失败者 数据清洗失败: HTTP 400 bad date")]
    assert actors[0].existing_overview == "旧简介", "失败不得改内存值"


def test_clean_success_updates_state_not_failed_list():
    actors = [_make_actor("成功者")]
    ns, logs, dialog_cls = _fake_dialog(actors)
    dialog_cls._on_clean_actor_done(ns, "id-成功者", True, "✅ 成功者 数据清洗成功")
    assert ns._clean_failed == []
    assert actors[0].existing_overview == "清洗后"


def test_clean_finished_dialog_lists_failures_and_retry_hint(monkeypatch):
    """完成弹窗: 失败名单 + 重试指引 + 「无需再点同步」说明。"""
    actors = [_make_actor("甲"), _make_actor("乙")]
    ns, logs, dialog_cls = _fake_dialog(actors)
    ns._clean_failed = [("id-甲", "m1"), ("id-乙", "m2")]

    captured: dict = {}

    class _Box:
        @staticmethod
        def information(parent, title, text):
            captured["title"] = title
            captured["text"] = text

    monkeypatch.setattr("mdcx.tools.emby_actor_manager_ui.QMessageBox", _Box)
    dialog_cls._on_clean_finished(ns, 8, 2)

    assert "甲" in captured["text"] and "乙" in captured["text"], "弹窗须列失败者名字"
    assert "再点一次「数据清洗」" in captured["text"], "须给出重试指引"
    assert ns._clean_failed == [], "汇总后清空失败缓存"


def test_clean_finished_zero_fail_explains_direct_write(monkeypatch):
    """全部成功: 明确告知已直接写入服务器, 无需点同步 (消除 #162 误解)。"""
    ns, logs, dialog_cls = _fake_dialog([_make_actor("甲")])
    captured: dict = {}

    class _Box:
        @staticmethod
        def information(parent, title, text):
            captured["text"] = text

    monkeypatch.setattr("mdcx.tools.emby_actor_manager_ui.QMessageBox", _Box)
    dialog_cls._on_clean_finished(ns, 10, 0)
    assert "无需再点「开始全部更新同步」" in captured["text"]


def test_actor_done_signal_carries_message():
    """AST 哨兵: 线程 actor_done 声明为 (str, bool, str) 且 run 的回调透传 msg。"""
    import ast
    import inspect

    from mdcx.tools.emby_actor_manager_ui import CleanDataThread

    src = inspect.getsource(CleanDataThread)
    tree = ast.parse(src.replace("class CleanDataThread", "class CleanDataThread", 1))
    assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "actor_done"
    ]
    assert len(assigns) == 1
    call = assigns[0].value
    assert isinstance(call, ast.Call)
    arg_types = [ast.unparse(a) for a in call.args]
    assert arg_types == ["str", "bool", "str"], f"信号须携带原因字符串, 实际: {arg_types}"

    # run() 内 emit 必须传 3 个实参 (含 msg)
    run_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run")
    emits = [
        ast.unparse(node)
        for node in ast.walk(run_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "emit"
        and len(node.args) == 3
    ]
    assert emits, "run 中 actor_done.emit 必须透传失败原因 (3 实参)"
