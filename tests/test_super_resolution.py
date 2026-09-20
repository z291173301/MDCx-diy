"""议题 #26 回归：海报超分（触发条件/参数/原子替换/失败降级），全程离线。"""

from __future__ import annotations

import asyncio

import pytest
from PIL import Image

from mdcx.config.manager import manager
from mdcx.config.resources import resources
from mdcx.core import super_resolution as sr


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "u", lambda rel: tmp_path / rel)
    monkeypatch.setattr(manager.config, "poster_sr_enabled", True)
    monkeypatch.setattr(manager.config, "poster_sr_preset", "realesr-photo-4x")
    monkeypatch.setattr(manager.config, "poster_sr_max_dim", 1200)
    yield


def _make_jpg(path, w: int, h: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), (120, 60, 200)).save(path, "JPEG")


def test_trigger_requires_switch_small_dim_and_size_guard(tmp_path, monkeypatch):
    small = tmp_path / "poster_small.jpg"
    _make_jpg(small, 300, 450)
    big = tmp_path / "poster_big.jpg"
    _make_jpg(big, 2000, 3000)

    assert sr._sr_trigger_ok(small) is True
    assert sr._sr_trigger_ok(big) is False, "最长边超阈值不超分"
    assert sr._sr_trigger_ok(tmp_path / "missing.jpg") is False

    monkeypatch.setattr(manager.config, "poster_sr_enabled", False)
    assert sr._sr_trigger_ok(small) is False, "开关默认关时绝不触发"


def test_build_args_per_preset():
    a = sr.build_args(sr.Path("/tmp/i.jpg"), sr.Path("/tmp/o.jpg"), sr.SrPreset.REALESR_PHOTO_4X)
    assert ["-n", "realesrgan-x4plus"] == a[a.index("-n") : a.index("-n") + 2]
    assert "-s" in a and a[a.index("-s") + 1] == "4"

    b = sr.build_args(sr.Path("/tmp/i.jpg"), sr.Path("/tmp/o.jpg"), sr.SrPreset.WAIFU_PHOTO_2X)
    assert ["-m", "upconv_7_photo"] == b[b.index("-m") : b.index("-m") + 2]
    assert b[b.index("-s") + 1] == "2"


def test_download_urls_cover_three_platforms():
    for tool in ("realesrgan", "waifu2x"):
        urls = sr._TOOL_DOWNLOAD_URLS[tool]
        assert set(urls) == {"macos", "linux", "windows"}
        assert all(u.startswith("https://github.com/") for u in urls.values())


@pytest.mark.asyncio
async def test_ensure_binary_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "plan9")
    assert await sr.ensure_binary("realesrgan") is None


@pytest.mark.asyncio
async def test_maybe_upscale_replaces_atomically(tmp_path, monkeypatch):
    poster = tmp_path / "poster.jpg"
    _make_jpg(poster, 300, 450)
    original = poster.read_bytes()

    async def fake_upscale(src, dst, preset):
        Image.new("RGB", (1200, 1800), (0, 0, 0)).save(dst, "JPEG")
        return True

    monkeypatch.setattr(sr, "upscale_image", fake_upscale)
    assert await sr.maybe_upscale_poster(poster) is True
    assert max(Image.open(poster).size) == 1800
    leftovers = [p for p in tmp_path.rglob("*") if "[SR]" in p.name]
    assert leftovers == [], "临时产物须清理"
    assert original != poster.read_bytes()


@pytest.mark.asyncio
async def test_maybe_upscale_rejects_non_enlarged_output(tmp_path, monkeypatch):
    poster = tmp_path / "poster.jpg"
    _make_jpg(poster, 300, 450)
    before = poster.read_bytes()

    async def fake_upscale(src, dst, preset):
        Image.new("RGB", (300, 450), (0, 0, 0)).save(dst, "JPEG")
        return True

    monkeypatch.setattr(sr, "upscale_image", fake_upscale)
    assert await sr.maybe_upscale_poster(poster) is False
    assert poster.read_bytes() == before, "产物未增大必须弃用并保持原图"


@pytest.mark.asyncio
async def test_maybe_upscale_failure_keeps_original(tmp_path, monkeypatch):
    poster = tmp_path / "poster.jpg"
    _make_jpg(poster, 300, 450)
    before = poster.read_bytes()

    async def fake_upscale(src, dst, preset):
        return False

    monkeypatch.setattr(sr, "upscale_image", fake_upscale)
    assert await sr.maybe_upscale_poster(poster) is False
    assert poster.read_bytes() == before


@pytest.mark.asyncio
async def test_upscale_image_timeout_degrades(monkeypatch, tmp_path):
    src = tmp_path / "a.jpg"
    dst = tmp_path / "b.jpg"
    _make_jpg(src, 10, 10)

    class _HangProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(30)
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return None

    async def fake_exec(*args, **kwargs):
        return _HangProc()

    monkeypatch.setattr(sr.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sr, "ensure_binary", lambda tool: _pass())
    monkeypatch.setattr(sr, "_SR_TIMEOUT_SECONDS", 0.05)

    assert await sr.upscale_image(src, dst, sr.SrPreset.REALESR_PHOTO_4X) is False


async def _pass():
    from pathlib import Path

    return Path("/bin/true")


def test_scraper_hooks_upscale_after_poster_success():
    """AST 哨兵：poster 下载成功后必须挂 maybe_upscale_poster（防未来重构丢钩子）。"""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "mdcx/core/scraper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and any("maybe_upscale_poster" in ast.unparse(s) for s in ast.walk(n))
    )
    text = ast.unparse(fn)
    idx_hook = text.index("maybe_upscale_poster")
    idx_check = text.index("poster_task.result()")
    assert idx_hook > idx_check, "超分必须在 poster 成功判定之后"
    assert "contextlib.suppress" in text, "超分异常不得影响刮削结果"
