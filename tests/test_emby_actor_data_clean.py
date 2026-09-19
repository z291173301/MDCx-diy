"""议题 #149: 演员数据清洗——简介噪声(`<br>`/`=====` 段落标题/占位文案)与非法生日。

- clean_overview_text 纯函数: 四类噪声规则、幂等、正常文本(含英文逗号)不动
- EMbyActressInfo.dump() 出口统一套用清洗(增量写入不再产生噪声)
- update_person_info 对存量脏简介先清洗再写回(同一出口)
- scan_actor_data_noise 扫描口径(脏简介/非法生日/Emby 零值不算噪声)
- actress_db 不再写入 minnano 占位文案(断「占位→判缺→重抓→再写占位」死循环)
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

from mdcx.models.emby import EMbyActressInfo, clean_overview_text

pytestmark = pytest.mark.asyncio


class TestCleanOverviewText:
    def test_br_variants_preserved(self):
        """议题 #171: <br> 属合法结构, 客户端渲染分行, 原样保留不压成逗号。"""
        assert clean_overview_text("中文名: 葵つばさ<br>罗马音: Aihara") == "中文名: 葵つばさ<br>罗马音: Aihara"
        for br in ("<br/>", "<br />", "<BR>", "<Br/>"):
            assert clean_overview_text(f"a{br}b") == f"a{br}b", br

    def test_section_headers_preserved(self):
        """===== 段落标题是合法分段结构, 原样保留（#171 实证 Jellyfin 靠其分行渲染）。"""
        text = "简介文本<br>===== 个人资料 =====<br>中文名: X<br>===== 外部链接 =====<br>TheMovieDb: https://x"
        assert clean_overview_text(text) == text

    def test_screenshot_external_links_shape(self):
        """议题 #149 截图1 实貌: 整段以外部链接段落标题开头, 链接内容保留。"""
        text = "<br>===== 外部链接 =====<br>TheMovieDb: https://www.themoviedb.org/person/42257 <br>"
        assert clean_overview_text(text) == text

    def test_placeholder_cleared(self):
        assert clean_overview_text("无维基百科信息, 从 minnano-av 数据库补全女优信息") == ""
        assert clean_overview_text("无维基百科信息，从 minnano-av 数据库补全女优信息") == ""

    def test_newlines_preserved(self):
        """议题 #171: 换行符保留, 不压成逗号（客户端渲染分行列表）。"""
        assert clean_overview_text("a\nb\r\nc") == "a\nb\r\nc"

    def test_placeholder_mixed_with_clean_content(self):
        """占位文案夹在合法内容之间: 删占位, 保留剩余结构与换行。"""
        assert (
            clean_overview_text("正常段\n无维基百科信息, 从 minnano-av 数据库补全女优信息\n外部段")
            == "正常段\n\n外部段"
        )

    def test_normal_text_untouched(self):
        """正常英文简介(含 ASCII 逗号)必须原样保留, 不被折叠成中文逗号。"""
        text = "English theatre and film director and producer, known for X."
        assert clean_overview_text(text) == text

    def test_idempotent(self):
        text = "简介<br>===== 个人资料 =====<br>中文名: X，已有逗号"
        once = clean_overview_text(text)
        assert clean_overview_text(once) == once

    def test_non_str_returns_empty(self):
        assert clean_overview_text(None) == ""
        assert clean_overview_text(123) == ""


def test_dump_cleans_overview():
    """dump() 出口统一清洗: 占位文案清除, 合法结构(<br>/换行/段标题)原样保留(#171)。"""
    info = EMbyActressInfo(name="x", server_id="s", id="i", overview="a<br>===== 外部链接 =====<br>b")
    assert info.dump()["Overview"] == "a<br>===== 外部链接 =====<br>b"


class _JsonResp:
    """伪造 httpx.Response 的轻量子集 (轻量直连 _emby_request 返回它)。"""

    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = ""

    def json(self):
        return self._json_data


async def _capture_update_payload(monkeypatch: pytest.MonkeyPatch, actor) -> dict:
    from mdcx.config.manager import manager
    from mdcx.tools import emby_actor_manager

    captured: dict = {}

    async def handler(method, url, *, headers=None, data=None, token=None, **kwargs):
        captured["data"] = data
        return _JsonResp(204), ""

    monkeypatch.setattr(manager.config, "server_type", "emby")
    monkeypatch.setattr(manager.config, "emby_url", "http://127.0.0.1:8096")
    monkeypatch.setattr(manager.config, "api_key", "token")
    monkeypatch.setattr(emby_actor_manager, "_emby_request", handler)

    ok, msg = await emby_actor_manager.update_person_info(actor)
    assert ok is True, msg
    return json.loads(captured["data"])


async def test_update_person_info_cleans_legacy_overview(monkeypatch: pytest.MonkeyPatch):
    """议题 #149: 存量脏简介随同步自愈——清洗后经同一出口写回。"""
    from mdcx.tools.emby_actor_manager import ActorInfo

    actor = ActorInfo(
        name="测试",
        actor_id="id1",
        server_id="srv1",
        existing_overview="a<br>===== 个人资料 =====<br>b",
    )
    payload = await _capture_update_payload(monkeypatch, actor)
    assert payload["Overview"] == "a<br>===== 个人资料 =====<br>b"


async def test_update_person_info_placeholder_becomes_empty_and_omitted(monkeypatch: pytest.MonkeyPatch):
    """占位简介清洗后为空: 同步出口省略 Overview(显式清空由数据清洗按钮负责)。"""
    from mdcx.tools.emby_actor_manager import ActorInfo

    actor = ActorInfo(
        name="测试",
        actor_id="id1",
        server_id="srv1",
        existing_overview="无维基百科信息, 从 minnano-av 数据库补全女优信息",
    )
    payload = await _capture_update_payload(monkeypatch, actor)
    assert "Overview" not in payload


async def test_clean_actor_data_writes_empty_overview_and_resets_birth(monkeypatch: pytest.MonkeyPatch):
    """议题 #149: 清洗路径显式写空简介(清占位) + 非法生日重置为 Emby 零值。"""
    from mdcx.config.manager import manager
    from mdcx.tools import emby_actor_manager
    from mdcx.tools.emby_actor_manager import ActorInfo

    captured: dict = {}

    async def handler(method, url, *, headers=None, data=None, token=None, **kwargs):
        captured["data"] = data
        return _JsonResp(204), ""

    monkeypatch.setattr(manager.config, "server_type", "emby")
    monkeypatch.setattr(manager.config, "emby_url", "http://127.0.0.1:8096")
    monkeypatch.setattr(manager.config, "api_key", "token")
    monkeypatch.setattr(emby_actor_manager, "_emby_request", handler)

    actor = ActorInfo(
        name="测试",
        actor_id="id1",
        server_id="srv1",
        existing_overview="无维基百科信息, 从 minnano-av 数据库补全女优信息",
        existing_premiere_date="0000-00-00T00:00:00.0000000Z",
    )
    ok, msg = await emby_actor_manager.clean_actor_data(actor, "", fix_birth=True)
    assert ok is True, msg
    payload = json.loads(captured["data"])
    assert payload["Overview"] == ""
    assert payload["PremiereDate"] == "0001-01-01T00:00:00.0000000Z"
    # 议题 #148 防线: Genres/Tags/ProviderIds 恒为集合, 不得缺省
    assert payload["Genres"] == [] and payload["Tags"] == [] and payload["ProviderIds"] == {}


def test_scan_actor_data_noise():
    """扫描口径: 脏简介与非法生日入选; 正常值、空值、Emby 零值(0001-01-01)不算噪声。"""
    from mdcx.tools.emby_actor_manager import ActorInfo
    from mdcx.tools.emby_actor_manager_ui import scan_actor_data_noise

    clean = ActorInfo(
        name="正常",
        actor_id="1",
        server_id="s",
        has_overview=True,
        existing_overview="正常简介",
        existing_premiere_date="1984-08-22T00:00:00.0000000Z",
    )
    emby_zero = ActorInfo(
        name="零值", actor_id="2", server_id="s", existing_premiere_date="0001-01-01T00:00:00.0000000Z"
    )
    # 议题 #171: <br>/换行属合法结构, 不算脏(清洗不再压平), 不进噪声名单
    dirty_ov = ActorInfo(
        name="占位", actor_id="3", server_id="s", existing_overview="无维基百科信息, 从 minnano-av 数据库补全女优信息"
    )
    bad_birth = ActorInfo(
        name="坏生日", actor_id="4", server_id="s", existing_premiere_date="0000-00-00T00:00:00.0000000Z"
    )

    items = scan_actor_data_noise([clean, emby_zero, dirty_ov, bad_birth])
    by_id = {a.actor_id: (ov, fix) for a, ov, fix in items}
    assert set(by_id) == {"3", "4"}
    assert by_id["3"] == ("", False)
    assert by_id["4"] == ("", True)


def test_actress_db_no_longer_writes_placeholder():
    """议题 #149: minnano 命中无简介时不再写占位文案(死循环根源)。"""
    import inspect

    from mdcx.tools import actress_db

    assert "从 minnano-av 数据库补全" not in inspect.getsource(actress_db)
