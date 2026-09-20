"""议题 #87 回归：演员数据源测试线程不得自建一次性事件循环。

反模式：`ActorSourceTestThread.run` 曾自建 `asyncio.new_event_loop()` +
`run_until_complete` + `close()`。数据源测试复用共享 curl_cffi 异步客户端，
其 cffi 定时器被注册到该一次性 loop 上；`loop.close()` 后定时器仍触发，
curl_cffi 回调抛 "RuntimeError: Event loop is closed"，Windows 上弹
"Python-CFFI error"。正确范式是走共享后台执行器（app 持久后台循环，`_run_coro` / `executor.submit`）。
本测试用 AST 锁定：该方法的 `run` 不得再出现 new_event_loop / run_until_complete，
且必须走共享执行器。
"""

import ast
from pathlib import Path

_SRC = Path("mdcx/tools/emby_actor_manager_ui.py")


def _run_method_source() -> str:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ActorSourceTestThread":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "run":
                    return ast.get_source_segment(_SRC.read_text(encoding="utf-8"), item) or ""
    raise AssertionError("ActorSourceTestThread.run 未找到")


def test_actor_source_test_run_uses_shared_executor():
    src = _run_method_source()
    assert "_run_coro" in src or "executor.run" in src or "executor.submit" in src, (
        "数据源测试必须走共享后台执行器（app 持久事件循环）"
    )


def test_actor_source_test_run_no_throwaway_loop():
    src = _run_method_source()
    for bad in ("new_event_loop", "run_until_complete"):
        assert bad not in src, f"ActorSourceTestThread.run 仍含一次性事件循环 {bad!r}（议题 #87 反模式）"
