"""议题 #180 回归：演员详情「现有数据」简介把 `<br>` 还原为换行展示。

Emby 服务器上的简介以 `<br>` 分行（服务器端渲染需要，数据本身正确），
MDCx 详情对话框此前按纯文本直出，用户看到满屏 `<br>` 标签。
修复仅在展示层替换，数据对象与同步 payload 不受影响。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

from PyQt6.QtWidgets import QApplication

_app: QApplication | None = None


def _ensure_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_actor(**kw):
    from mdcx.tools.emby_actor_manager import ActorInfo

    base = {"name": "高桥惠子", "actor_id": "id1", "server_id": "srv1"}
    base.update(kw)
    return ActorInfo(**base)


def test_detail_dialog_renders_br_as_newline():
    _ensure_app()
    from mdcx.tools.emby_actor_manager_ui import ActorDetailDialog

    actor = _make_actor(existing_overview="简介文本<br>===== 个人资料 =====<br>出生: 1955年1月22日<br/>生日")
    dlg = ActorDetailDialog(actor)
    text = dlg.existing_info.toPlainText()
    assert "<br" not in text.lower()
    assert "简介文本\n===== 个人资料 =====\n出生: 1955年1月22日\n生日" in text
    # 数据本体不变：同步 payload 仍带 <br>（服务器渲染依赖原样）
    assert actor.existing_overview == "简介文本<br>===== 个人资料 =====<br>出生: 1955年1月22日<br/>生日"
    dlg.deleteLater()


def test_detail_dialog_br_case_and_space_variants():
    _ensure_app()
    from mdcx.tools.emby_actor_manager_ui import ActorDetailDialog

    actor = _make_actor(existing_overview="a<BR>b<Br/>c<br />d")
    dlg = ActorDetailDialog(actor)
    assert dlg.existing_info.toPlainText().startswith("简介: a\nb\nc\nd\n")
    dlg.deleteLater()
