"""议题 #176：wiki 个人资料 infobox 按行配对，不再全局点数 th/td。

日文 ActorActress 模板的 <td> 带 text-align 样式，旧实现
`find_all("td", style="", colspan=False)` 一个都拿不到，keys=5/values=0
触发「表格列数不匹配」整表跳过，生日/出生地全丢。
"""

from __future__ import annotations

import bs4

from mdcx.config.enums import EmbyAction
from mdcx.models.emby import EMbyActressInfo
from mdcx.tools.wiki import _process_personal_profile


def _output(infobox_html: str) -> bs4.Tag:
    soup = bs4.BeautifulSoup(f'<div class="mw-parser-output">{infobox_html}</div>', "lxml")
    out = soup.find(class_="mw-parser-output")
    assert isinstance(out, bs4.Tag)
    return out


def _info(name: str = "测试") -> EMbyActressInfo:
    info = EMbyActressInfo(name=name, server_id="s", id="i")
    # parse_detail 在调用 _process_personal_profile 前统一先置默认出生地
    info.locations = ["日本"]
    return info


JA_FUJIMOTO = """
<table class="infobox">
<tr><th colspan="2">ふじもと きょうこ<br/>藤本 恭子</th></tr>
<tr><th scope="row">生年月日</th>
    <td style="text-align:left;"><span style="display:none"> (<span class="bday">1971-01-19</span>) </span>1971年1月19日（55歳）</td></tr>
<tr><th scope="row">出身地</th><td style="text-align:left;">日本</td></tr>
<tr><th scope="row">職業</th><td style="text-align:left;">女優</td></tr>
<tr><th scope="row">ジャンル</th><td style="text-align:left;">テレビドラマ</td></tr>
<tr><th scope="row">活動期間</th><td style="text-align:left;">1986年-</td></tr>
<tr class="noprint"><td colspan="2" style="text-align:right; font-size:85%;">テンプレートを表示</td></tr>
</table>
"""

ZH_OGAWA = """
<table class="infobox vcard plainlist">
<tr><th colspan="2">小川 真奈</th></tr>
<tr><th scope="row">出生</th><td style="">(1993-07-02)1993年7月2日（33岁）</td></tr>
<tr><th scope="row">出身地</th><td style="">日本 埼玉县</td></tr>
<tr><th scope="row">血型</th><td style="">B型</td></tr>
<tr><th>日语写法</th></tr>
<tr><th scope="row">日语原文</th><td style="text-align:left;;">小川 真奈</td></tr>
<tr><th scope="row">假名</th><td style="text-align:left;;">おがわ まな</td></tr>
<tr><td colspan="2" style="text-align:right;">模板 | 分类</td></tr>
</table>
"""

JA_KOMATSU_BIRTHPLACE = """
<table class="infobox">
<tr><th scope="row">生年月日</th>
    <td style="text-align:left;">1971年6月5日</td></tr>
<tr><th scope="row">出生地</th><td style="text-align:left;">日本, 福島県 いわき市</td></tr>
<tr><th>主な作品</th></tr>
<tr><td colspan="2" style="text-align:center;">テレビドラマ</td></tr>
</table>
"""


def test_ja_aligned_tds_are_parsed_not_skipped():
    """日文 infobox 的 td 全带 text-align，旧实现会整表跳过。"""
    info = _info("藤本恭子")
    overview = _process_personal_profile(_output(JA_FUJIMOTO), info, "", "https://ja.wikipedia.org/wiki/x", True, [])
    assert "表格列数不匹配" not in overview
    assert "生年月日" in overview
    assert info.birthday == "1971-01-19"
    assert info.year == "1971"
    assert info.locations == ["日本"]


def test_zh_mixed_style_tds_keep_all_rows():
    """中文 vcard 前几行 style=''、日语写法行带 text-align，按行配对后两边都在。"""
    info = _info("小川真奈")
    overview = _process_personal_profile(_output(ZH_OGAWA), info, "", "https://zh.wikipedia.org/wiki/x", False, [])
    assert "出生: " in overview
    assert "日语原文: " in overview
    assert info.birthday == "1993-07-02"
    assert info.locations == ["日本·埼玉县"]


def test_birthplace_label_and_section_header_rows():
    """「出生地」要当出身地；只有 th / 只有 colspan td 的分区行不得打断解析。"""
    info = _info("小松みゆき")
    emby_on = [EmbyAction.ACTOR_INFO_TRANSLATE, EmbyAction.ACTOR_INFO_ZH_CN]
    overview = _process_personal_profile(
        _output(JA_KOMATSU_BIRTHPLACE), info, "", "https://ja.wikipedia.org/wiki/x", True, emby_on
    )
    assert info.birthday == "1971-06-05"
    assert info.locations == ["日本·福岛县 いわき市"]
    assert "生年月日" in overview
    assert "出生地" in overview
