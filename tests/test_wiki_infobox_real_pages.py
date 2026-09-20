"""议题 #176 真实页面回归：用抓取自维基百科的 infobox 原样 HTML 锁定解析。

用户报告「个人资料表格列数不匹配」批量出现（藤本恭子/小松みゆき 等日文页、
小川真奈 等中文 vcard 页）。fixture 为生产实际会拿到的 mw-parser-output 内
infobox 表片段（ja.m.wikipedia 与桌面版结构一致），防止手造夹具与真实结构漂移。
"""

from __future__ import annotations

from pathlib import Path

import bs4

from mdcx.config.enums import EmbyAction
from mdcx.models.emby import EMbyActressInfo
from mdcx.tools.wiki import _process_personal_profile

_FIXTURES = Path(__file__).parent / "fixtures" / "wiki_infobox"


def _run(fixture: str, name: str, *, ja: bool, emby_on: list[EmbyAction]) -> tuple[EMbyActressInfo, str]:
    html = (_FIXTURES / fixture).read_text(encoding="utf-8")
    soup = bs4.BeautifulSoup(f'<div class="mw-parser-output">{html}</div>', "lxml")
    out = soup.find(class_="mw-parser-output")
    assert isinstance(out, bs4.Tag)
    info = EMbyActressInfo(name=name, server_id="s", id="i")
    info.locations = ["日本"]
    overview = _process_personal_profile(out, info, "", f"https://wiki/{name}", ja, emby_on)
    return info, overview


def test_real_ja_actoractress_fujimoto():
    """藤本恭子：td 全带 text-align:left，旧实现整表跳过 → 生日丢失。"""
    info, overview = _run("ja_actoractress_fujimoto.html", "藤本恭子", ja=True, emby_on=[])
    assert "===== 个人资料 =====" in overview
    assert "生年月日" in overview and "職業" in overview
    assert info.birthday == "1971-01-19"
    assert info.year == "1971"
    assert info.locations == ["日本"]


def test_real_ja_actoractress_komatsu():
    """小松みゆき：「出生地」标签旧代码不识别；「日本,福島県」前缀剥离后取到县名。"""
    emby_on = [EmbyAction.ACTOR_INFO_TRANSLATE, EmbyAction.ACTOR_INFO_ZH_CN]
    info, overview = _run("ja_actoractress_komatsu.html", "小松みゆき", ja=True, emby_on=emby_on)
    assert info.birthday == "1971-06-05"
    assert info.locations == ["日本·福岛县いわき市"]
    assert "別名義" in overview and "配偶者" in overview


def test_real_zh_vcard_ogawa():
    """小川真奈：vcard 混合 style（前几行空串、日语写法行带 text-align），全部行都应在。"""
    info, overview = _run("zh_vcard_ogawa.html", "小川真奈", ja=False, emby_on=[])
    assert info.birthday == "1993-07-02"
    assert info.locations == ["日本·埼玉县"]
    assert "出身地" in overview and "日语原文" in overview and "代表作" in overview
