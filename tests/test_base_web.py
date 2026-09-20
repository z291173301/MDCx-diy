from concurrent.futures import CancelledError
from pathlib import Path

import pytest

import mdcx.base.web as base_web
import mdcx.core.web as core_web
from mdcx.config.enums import DownloadableFile
from mdcx.config.manager import manager


class _FakeResponse:
    def __init__(self, url: str, headers: dict[str, str] | None = None, content: bytes = b"", status_code: int = 200):
        self.url = url
        self.headers = headers or {}
        self.content = content
        self.status_code = status_code


class _FakeComputed:
    class _AsyncClient:
        def request(self, method: str, url: str, **kwargs):
            return (method, url, kwargs)

    async_client = _AsyncClient()


class _FakeComputedLease:
    def __enter__(self):
        return _FakeComputed()

    def __exit__(self, exc_type, exc, traceback):
        return None


def test_normalize_media_url_removes_empty_query_and_probe_params():
    assert (
        base_web.normalize_media_url(
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg?&&&",
        )
        == "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg"
    )
    assert (
        base_web.normalize_media_url(
            "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg?&w=120&h=90&&",
            strip_dmm_probe_params=True,
        )
        == "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg"
    )


def test_normalize_media_url_collapses_duplicate_slashes_for_dmm_hosts():
    assert (
        base_web.normalize_media_url(
            "https://awsimgsrc.dmm.co.jp/pics_dig//digital/video/ssis00100/ssis00100pl.jpg",
        )
        == "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00100/ssis00100pl.jpg"
    )
    assert (
        base_web.normalize_media_url(
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00100/ssis00100pl.jpg",
        )
        == "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/ssis00100/ssis00100pl.jpg"
    )


def test_check_theporndb_api_token_handles_cancelled_executor(monkeypatch: pytest.MonkeyPatch):
    logs: list[str] = []

    def fake_run(_coro):
        raise CancelledError()

    monkeypatch.setattr(manager.config, "theporndb_api_token", "token")
    monkeypatch.setattr(base_web.manager, "acquire_computed", lambda: _FakeComputedLease())
    monkeypatch.setattr(base_web.executor, "run", fake_run)
    monkeypatch.setattr(base_web.signal, "show_log_text", logs.append)

    result = base_web.check_theporndb_api_token()

    assert result == "❌ ThePornDB 连接检查已取消"
    assert logs == ["❌ ThePornDB 连接检查已取消"]


@pytest.mark.asyncio
async def test_check_url_cleans_dmm_probe_params_from_final_url(monkeypatch: pytest.MonkeyPatch):
    async def fake_request(method: str, url: str, **kwargs):
        assert method == "GET"
        assert "w=120" in url
        assert "h=90" in url
        return (
            _FakeResponse(
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg?w=120&h=90&&",
                headers={"Content-Length": "4096"},
            ),
            "",
        )

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    result = await base_web.check_url("https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg?&&&")

    assert result == "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg"


@pytest.mark.asyncio
async def test_check_url_uses_config_retry_for_dmm_images(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append(kwargs)
        return None, "连接超时"

    async def fake_sleep(delay: float):
        return None

    monkeypatch.setattr(manager.config, "retry", 4)
    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)
    monkeypatch.setattr(base_web.asyncio, "sleep", fake_sleep)

    result = await base_web.check_url("https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg")

    assert result is None
    assert len(calls) == 4
    assert all(call["retry_count"] == 1 for call in calls)


@pytest.mark.asyncio
async def test_check_url_accepts_small_real_dmm_sample_image(monkeypatch: pytest.MonkeyPatch):
    # 议题 #106：120x90 探测缩略图下真实剧照最小约 2.8KB（IBW-786 系列 2.8-4.5KB），
    # 不能用封面体积标准（10KB+）当占位图阈值，否则整批剧照被误杀
    async def fake_request(method: str, url: str, **kwargs):
        return (
            _FakeResponse(
                "https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-8.jpg",
                headers={"Content-Length": "2868"},
            ),
            "",
        )

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    result = await base_web.check_url("https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-8.jpg")

    assert result == "https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-8.jpg"


@pytest.mark.asyncio
async def test_check_url_rejects_tiny_dmm_junk_response(monkeypatch: pytest.MonkeyPatch):
    async def fake_request(method: str, url: str, **kwargs):
        return (_FakeResponse(url, headers={"Content-Length": "142"}), "")

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    result = await base_web.check_url("https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-1.jpg")

    assert result is None


@pytest.mark.asyncio
async def test_get_url_content_length_uses_get_for_dmm_images(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str]] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return (_FakeResponse(url, headers={"Content-Length": "12345"}), "")

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    length = await base_web.get_url_content_length(
        "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg?&&"
    )

    assert length == 12345
    assert calls == [
        ("GET", "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg"),
    ]


@pytest.mark.asyncio
async def test_download_extrafanart_task_uses_direct_get_for_non_dmm_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    calls: list[tuple[str, str]] = []

    async def fake_get_content(url: str, **kwargs):
        calls.append(("get_content", url))
        return b"fake-image", ""

    async def fake_download(url: str, file_path: Path):
        calls.append(("download", url))
        return False

    async def fake_check_pic_async(path: Path):
        return (800, 1200)

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)
    monkeypatch.setattr(manager.computed.async_client, "download", fake_download)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        (
            "https://example.test/images/fanart1.jpg",
            tmp_path / "fanart1.jpg",
            tmp_path,
            "fanart1.jpg",
        )
    )

    assert result is True
    assert calls == [
        ("get_content", "https://example.test/images/fanart1.jpg"),
    ]


@pytest.mark.asyncio
async def test_download_extrafanart_task_uses_single_get_for_dmm_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return _FakeResponse(url, content=b"fake-image"), ""

    async def fake_check_pic_async(path: Path):
        return (800, 1200)

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        (
            "https://pics.dmm.co.jp/digital/video/pred00816/pred00816jp-1.jpg",
            tmp_path / "fanart1.jpg",
            tmp_path,
            "fanart1.jpg",
        )
    )

    assert result is True
    assert calls == [
        ("GET", "https://pics.dmm.co.jp/digital/video/pred00816/pred00816jp-1.jpg"),
    ]


@pytest.mark.asyncio
async def test_download_extrafanart_task_uses_jdbstatic_headers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    headers_seen: list[dict[str, str] | None] = []

    async def fake_get_content(url: str, **kwargs):
        headers_seen.append(kwargs.get("headers"))
        return b"fake-image", ""

    async def fake_check_pic_async(path: Path):
        return (800, 1200)

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        (
            "https://c0.jdbstatic.com/covers/xw/XWPga.jpg",
            tmp_path / "fanart1.jpg",
            tmp_path,
            "fanart1.jpg",
        )
    )

    assert result is True
    # spfcas 无水印变体失败（fake 内容非 JPEG）后回退网页版原图，该请求需带 jdbstatic headers
    jdbstatic_headers = [h for h in headers_seen if h is not None]
    assert jdbstatic_headers
    assert jdbstatic_headers[0]["Referer"] == "https://javdb.com/"
    assert "User-Agent" in jdbstatic_headers[0]


@pytest.mark.asyncio
async def test_download_extrafanart_task_skips_invalid_dmm_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return _FakeResponse("https://pics.dmm.co.jp/digital/video/pred00816/now_printing.jpg", content=b"fake"), ""

    async def fake_check_pic_async(path: Path):
        raise AssertionError("无效 DMM 图片不应写入后再验图")

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        (
            "https://pics.dmm.co.jp/digital/video/pred00816/pred00816jp-1.jpg",
            tmp_path / "fanart1.jpg",
            tmp_path,
            "fanart1.jpg",
        )
    )

    assert result is False
    assert calls == [("GET", "https://pics.dmm.co.jp/digital/video/pred00816/pred00816jp-1.jpg")]


@pytest.mark.asyncio
async def test_extrafanart_download_does_not_gate_batch_on_first_image_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    calls: list[str] = []

    async def fake_download_extrafanart_task(task):
        calls.append(task[0])
        return task[0].endswith("fanart2.jpg")

    monkeypatch.setattr(manager.config, "download_files", [DownloadableFile.EXTRAFANART])
    monkeypatch.setattr(manager.config, "keep_files", [])
    monkeypatch.setattr(core_web, "download_extrafanart_task", fake_download_extrafanart_task)

    result = await core_web.extrafanart_download(
        ["https://example.test/fanart1.jpg", "https://example.test/fanart2.jpg"],
        "test",
        tmp_path,
    )

    assert result is False
    assert calls == ["https://example.test/fanart1.jpg", "https://example.test/fanart2.jpg"]


# ============================================================
# JavDB App CDN（tp.spfcas.com）加密流解密
# 加密格式：首字节为随机 XOR key，其余字节与 key 异或；明文应为 JPEG（FF D8 起始）
# ============================================================


def test_is_spfcas_image_url_matches_by_domain():
    """按域名判定 App CDN；路径中段（如 rhe951l4q）可能变更，不参与判定"""
    assert base_web.is_spfcas_image_url("https://tp.spfcas.com/rhe951l4q/covers/z4/z4Z5rW.jpg")
    assert base_web.is_spfcas_image_url("https://tp.spfcas.com/otherssegment/covers/x.jpg")
    assert base_web.is_spfcas_image_url("//tp.spfcas.com/rhe951l4q/small_covers/z4/z4Z5rW.jpg")
    assert not base_web.is_spfcas_image_url("https://c0.jdbstatic.com/covers/xw/XWPga.jpg")
    assert not base_web.is_spfcas_image_url("https://tp.spfcas.com/rhe951l4q/index.html")
    assert not base_web.is_spfcas_image_url("")
    assert not base_web.is_spfcas_image_url("https://tp.spfcas.com")  # 无路径非图片


def test_decrypt_spfcas_image_roundtrip_and_validation():
    """解密回明文；非 JPEG 明文/空输入返回 None"""
    plain = b"\xff\xd8\xe0" + b"\x10JFIF" + b"\x00" * 32
    key = 0x5A
    enc = bytes([key]) + bytes(b ^ key for b in plain)
    assert base_web.decrypt_spfcas_image(enc) == plain

    bad_plain = b"\x89PNG" + b"data"
    enc_bad = bytes([key]) + bytes(b ^ key for b in bad_plain)
    assert base_web.decrypt_spfcas_image(enc_bad) is None

    assert base_web.decrypt_spfcas_image(b"") is None
    assert base_web.decrypt_spfcas_image(b"\x5a") is None


def test_decode_spfcas_image_content_passthrough_non_spfcas():
    """非 App CDN URL 内容原样透传，不做解密"""
    content = b"\x89PNG-raw-bytes"
    assert base_web.decode_spfcas_image_content("https://c0.jdbstatic.com/covers/xw/XWPga.jpg", content) == content
    assert base_web.decode_spfcas_image_content("https://pics.dmm.co.jp/digital/video/x.jpg", content) == content


@pytest.mark.asyncio
async def test_download_file_with_filepath_decrypts_spfcas_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """download_file_with_filepath 对 App CDN 加密流应解密后落盘"""
    plain = b"\xff\xd8" + b"\x00" * 64
    enc = bytes([0x33]) + bytes(b ^ 0x33 for b in plain)

    async def fake_get_content(url: str, **kwargs):
        return enc, ""

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)

    target = tmp_path / "cover.jpg"
    assert await base_web.download_file_with_filepath(
        "https://tp.spfcas.com/rhe951l4q/covers/z4/z4Z5rW.jpg", target, tmp_path
    )
    assert target.read_bytes() == plain


@pytest.mark.asyncio
async def test_download_file_with_filepath_rejects_bad_spfcas_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """App CDN 内容解密失败（非 JPEG 明文）应判下载失败且不写盘"""

    async def fake_get_content(url: str, **kwargs):
        return b"\x33notjpegdata", ""

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)

    target = tmp_path / "cover.jpg"
    assert not await base_web.download_file_with_filepath(
        "https://tp.spfcas.com/rhe951l4q/covers/z4/z4Z5rW.jpg", target, tmp_path
    )
    assert not target.exists()


@pytest.mark.asyncio
async def test_download_content_with_filepath_decrypts_spfcas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """download_content_with_filepath 对 App CDN 加密流应解密后落盘"""
    plain = b"\xff\xd8" + b"\x01" * 32
    enc = bytes([0x77]) + bytes(b ^ 0x77 for b in plain)

    async def fake_get_content(url: str, **kwargs):
        return enc, ""

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)

    target = tmp_path / "fanart.jpg"
    assert await base_web.download_content_with_filepath(
        "https://tp.spfcas.com/rhe951l4q/samples/z4/z4Z5rW_1.jpg", target, tmp_path
    )
    assert target.read_bytes() == plain


# ============================================================
# 网页版 CDN → App CDN 无水印 URL 变换（javdb 系爬虫图源升级）
# ============================================================


def test_jdbstatic_to_spfcas_mapping():
    """网页版 URL 变换为 App CDN URL：covers/samples 保持、thumbs→small_covers、插入当前中段"""
    assert (
        base_web.jdbstatic_to_spfcas("https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg")
        == f"https://tp.spfcas.com/{base_web._spfcas_segment()}/covers/z4/z4Z5rW.jpg"
    )
    assert (
        base_web.jdbstatic_to_spfcas("https://c0.jdbstatic.com/thumbs/xz/XzkY4.jpg")
        == f"https://tp.spfcas.com/{base_web._spfcas_segment()}/small_covers/xz/XzkY4.jpg"
    )
    assert (
        base_web.jdbstatic_to_spfcas("https://c0.jdbstatic.com/samples/z4/1.jpg")
        == f"https://tp.spfcas.com/{base_web._spfcas_segment()}/samples/z4/1.jpg"
    )
    # 协议相对地址
    assert base_web.jdbstatic_to_spfcas("//c0.jdbstatic.com/covers/z4/z4Z5rW.jpg").startswith("https://tp.spfcas.com/")
    # 形态不符返回空串
    assert base_web.jdbstatic_to_spfcas("https://pics.dmm.co.jp/digital/video/x.jpg") == ""
    assert base_web.jdbstatic_to_spfcas("") == ""
    assert base_web.jdbstatic_to_spfcas("https://c0.jdbstatic.com/page.html") == ""


def test_spfcas_to_jdbstatic_roundtrip():
    """App CDN URL 逆向变换回网页版 URL（供尺寸探测回退），与正向变换互逆"""
    jdbstatic = "https://c0.jdbstatic.com/thumbs/xz/XzkY4.jpg"
    spfcas = base_web.jdbstatic_to_spfcas(jdbstatic)
    assert base_web.spfcas_to_jdbstatic(spfcas) == jdbstatic

    jdbstatic_cover = "https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg"
    assert base_web.spfcas_to_jdbstatic(base_web.jdbstatic_to_spfcas(jdbstatic_cover)) == jdbstatic_cover

    # 形态不符返回空串
    assert base_web.spfcas_to_jdbstatic("https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg") == ""
    assert base_web.spfcas_to_jdbstatic("") == ""


def test_learn_spfcas_image_segment_updates_mapping():
    """从 App CDN URL 学习当前中段后，变换应使用新段；非法 URL 不改变已有学习值"""
    original_segment = base_web._spfcas_segment()
    try:
        assert base_web.learn_spfcas_image_segment("https://tp.spfcas.com/newseg9/covers/z4/z4Z5rW.jpg") == "newseg9"
        assert base_web._spfcas_segment() == "newseg9"
        assert base_web.jdbstatic_to_spfcas("https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg") == (
            "https://tp.spfcas.com/newseg9/covers/z4/z4Z5rW.jpg"
        )

        # 非法 URL 不更新
        assert base_web.learn_spfcas_image_segment("https://tp.spfcas.com/newseg9/page.html") == ""
        assert base_web.learn_spfcas_image_segment("https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg") == ""
        assert base_web.learn_spfcas_image_segment("") == ""
        assert base_web._spfcas_segment() == "newseg9"
    finally:
        base_web.reset_spfcas_segment_for_test(original_segment)


def test_prepend_spfcas_candidates_keeps_original_as_fallback():
    """thumb 候选变换：jdbstatic 候选前置 spfcas 变体并保留原图保底；其他来源原样"""
    covers = [
        ("javdb", "https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg"),
        ("dmm", "https://pics.dmm.co.jp/digital/video/x/xps.jpg"),
        ("javdb", ""),
    ]
    result = core_web._prepend_spfcas_candidates(covers)

    assert result[0] == ("javdb", f"https://tp.spfcas.com/{base_web._spfcas_segment()}/covers/z4/z4Z5rW.jpg")
    assert result[1] == ("javdb", "https://c0.jdbstatic.com/covers/z4/z4Z5rW.jpg")
    assert result[2] == ("dmm", "https://pics.dmm.co.jp/digital/video/x/xps.jpg")
    assert result[3] == ("javdb", "")


@pytest.mark.asyncio
async def test_download_extrafanart_task_uses_spfcas_variant_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """剧照下载：spfcas 变体命中时解密落盘，且不再请求网页版原图"""
    plain = b"\xff\xd8" + b"\x02" * 16
    enc = bytes([0x11]) + bytes(b ^ 0x11 for b in plain)
    requested: list[str] = []

    async def fake_get_content(url: str, **kwargs):
        requested.append(url)
        return enc, ""

    async def fake_check_pic_async(path: Path):
        return (800, 600)

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        ("https://c0.jdbstatic.com/samples/z4/1.jpg", tmp_path / "fanart1.jpg", tmp_path, "fanart1.jpg")
    )

    assert result is True
    assert len(requested) == 1
    assert requested[0].startswith("https://tp.spfcas.com/")
    assert (tmp_path / "fanart1.jpg").read_bytes() == plain


@pytest.mark.asyncio
async def test_download_extrafanart_task_falls_back_to_original(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """剧照下载：spfcas 变体失败（如中段过期 404）时回退网页版原图"""
    requested: list[str] = []

    async def fake_get_content(url: str, **kwargs):
        requested.append(url)
        if "tp.spfcas.com" in url:
            return None, "HTTP 404"
        return b"\xff\xd8fallback-jpeg", ""

    async def fake_check_pic_async(path: Path):
        return (800, 600)

    monkeypatch.setattr(manager.computed.async_client, "get_content", fake_get_content)
    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        ("https://c0.jdbstatic.com/samples/z4/1.jpg", tmp_path / "fanart1.jpg", tmp_path, "fanart1.jpg")
    )

    assert result is True
    assert requested[0].startswith("https://tp.spfcas.com/")
    assert requested[1] == "https://c0.jdbstatic.com/samples/z4/1.jpg"
    assert (tmp_path / "fanart1.jpg").read_bytes() == b"\xff\xd8fallback-jpeg"


@pytest.mark.asyncio
async def test_build_poster_candidates_size_probe_falls_back_to_jdbstatic(monkeypatch: pytest.MonkeyPatch):
    """auto_best 选优：spfcas 变体尺寸探测失败时用逆向网页版 URL 探测尺寸"""
    from mdcx.core import web as core_web_mod

    async def fake_get_imgsize(url: str) -> tuple[int, int]:
        if "tp.spfcas.com" in url:
            return (0, 0)  # 加密流无法解析尺寸
        return (800, 1200)  # 网页版同图可探测

    monkeypatch.setattr(core_web_mod, "get_imgsize", fake_get_imgsize)

    candidates = [core_web_mod.PosterCandidate("javdb", "https://tp.spfcas.com/rhe951l4q/covers/z4/z4Z5rW.jpg", True)]
    sized = await core_web_mod._sized_poster_candidates(candidates, media_context=None)

    assert len(sized) == 1
    assert sized[0].size == (800, 1200)


def test_dmm_aws_to_pics_fallback_url():
    """AWS 无水印 URL 反向为 pics.dmm.co.jp 原图 host，形态不符返回空串。"""
    assert (
        base_web.dmm_aws_to_pics_fallback_url(
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/504ibw00786z/504ibw00786z-6.jpg"
        )
        == "https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-6.jpg"
    )
    # 已是 pics 原图 / 非 awsimgsrc 形态 → 无回退目标
    assert base_web.dmm_aws_to_pics_fallback_url("https://pics.dmm.co.jp/digital/video/x/x.jpg") == ""
    assert base_web.dmm_aws_to_pics_fallback_url("https://awsimgsrc.dmm.co.jp/other/x.jpg") == ""


@pytest.mark.asyncio
async def test_download_extrafanart_task_falls_back_to_pics_when_aws_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """剧照 AWS 无水印下载 404 时，回退 pics.dmm.co.jp 原图重试。"""
    requested: list[str] = []

    async def fake_dmm_download(url: str, file_path: Path, folder_new_path: Path) -> bool:
        requested.append(url)
        if "awsimgsrc.dmm.co.jp" in url:
            return False  # AWS 404
        return True

    monkeypatch.setattr(base_web, "download_dmm_extrafanart_with_filepath", fake_dmm_download)

    async def fake_check_pic_async(path: Path):
        return (800, 600)

    monkeypatch.setattr(base_web, "check_pic_async", fake_check_pic_async)

    result = await base_web.download_extrafanart_task(
        (
            "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/504ibw00786z/504ibw00786z-6.jpg",
            tmp_path / "fanart1.jpg",
            tmp_path,
            "fanart1.jpg",
        )
    )

    assert result is True
    assert requested == [
        "https://awsimgsrc.dmm.co.jp/pics_dig/digital/video/504ibw00786z/504ibw00786z-6.jpg",
        "https://pics.dmm.co.jp/digital/video/504ibw00786z/504ibw00786z-6.jpg",
    ]


def test_parse_content_range_total():
    """议题 #30: Content-Range 总大小解析, `/*` 与畸形返回 None。"""
    assert base_web._parse_content_range_total("bytes 0-1023/512345") == 512345
    assert base_web._parse_content_range_total("bytes 0-0/28") == 28
    assert base_web._parse_content_range_total("bytes 0-1023/*") is None
    assert base_web._parse_content_range_total(None) is None
    assert base_web._parse_content_range_total("garbage") is None


@pytest.mark.asyncio
async def test_check_url_dmm_uses_range_probe_and_content_range(monkeypatch: pytest.MonkeyPatch):
    """议题 #30: DMM 探测带 Range+stream 只传头部, 总大小取 Content-Range, 不再整图下载。"""
    seen: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        seen.append(kwargs)
        return (
            _FakeResponse(
                "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499pl.jpg?w=120&h=90",
                headers={"Content-Range": "bytes 0-1023/512345"},
            ),
            "",
        )

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    size = await base_web.check_url(
        "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499pl.jpg", length=True
    )

    assert size == 512345
    assert seen[0]["stream"] is True
    assert seen[0]["headers"]["Range"] == "bytes=0-1023"


@pytest.mark.asyncio
async def test_check_url_dmm_falls_back_to_full_get_without_size_headers(monkeypatch: pytest.MonkeyPatch):
    """议题 #30: 服务器忽略 Range 且不给任何大小头时, 回退一次非流式完整下载判定。"""
    calls: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return _FakeResponse(url, headers={}), ""
        return _FakeResponse(url, headers={}, content=b"x" * 4096), ""

    monkeypatch.setattr(manager.computed.async_client, "request", fake_request)

    result = await base_web.check_url("https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg")

    assert result == "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499ps.jpg"
    assert len(calls) == 2
    assert calls[0].get("stream") is True
    assert not calls[1].get("stream")
