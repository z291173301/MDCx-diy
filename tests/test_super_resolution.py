"""议题 #26 回归：海报超分（触发条件/参数/原子替换/失败降级），全程离线。"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import zipfile
from io import BytesIO

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


def test_realesrgan_linux_asset_uses_ubuntu_suffix():
    """#26: Real-ESRGAN 官方 Linux 资产名为 ubuntu，拼成 linux.zip 实测 404；waifu2x 才是 linux。"""
    assert sr._TOOL_DOWNLOAD_URLS["realesrgan"]["linux"].endswith("-ubuntu.zip")
    assert sr._TOOL_DOWNLOAD_URLS["waifu2x"]["linux"].endswith("-linux.zip")


def test_checksum_matches_when_baseline_configured(monkeypatch):
    data = b"zip-bytes"
    monkeypatch.setattr(sr, "_TOOL_CHECKSUMS", {"realesrgan": {"linux": hashlib.sha256(data).hexdigest()}})
    assert sr._checksum_matches(data, "realesrgan", "linux") is True


def test_checksum_detects_mismatch(monkeypatch):
    monkeypatch.setattr(sr, "_TOOL_CHECKSUMS", {"realesrgan": {"linux": "0" * 64}})
    assert sr._checksum_matches(b"zip-bytes", "realesrgan", "linux") is False


def test_checksum_passes_without_baseline(monkeypatch):
    """无基准时不拦截，仅靠解压后可执行性兜底。"""
    monkeypatch.setattr(sr, "_TOOL_CHECKSUMS", {})
    assert sr._checksum_matches(b"anything", "realesrgan", "linux") is True


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


def _make_tool_zip(tool: str) -> bytes:
    """构造官方形态的 zip：顶层目录 `<tool>/`，二进制名为 `<tool>-ncnn-vulkan`。"""
    name = f"{tool}-ncnn-vulkan.exe" if sys.platform == "win32" else f"{tool}-ncnn-vulkan"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{tool}/{name}", b"fake binary")
    return buffer.getvalue()


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


@pytest.mark.asyncio
async def test_ensure_binary_prefers_builtin_without_download(tmp_path, monkeypatch):
    """一体包（_MEIPASS）内置工具时直接释放使用，不触发下载。"""
    builtin = tmp_path / "mei" / "sr_tools" / "realesrgan"
    builtin.mkdir(parents=True)
    (builtin / "realesrgan-ncnn-vulkan").write_text("#!/bin/sh\nexit 0")
    monkeypatch.setattr(sr.sys, "_MEIPASS", str(tmp_path / "mei"), raising=False)

    async def fail_download(url):
        raise AssertionError("内置工具可用时不应发起下载")

    monkeypatch.setattr(sr, "_download_bytes", fail_download)
    assert await sr.ensure_binary("realesrgan") is not None


@pytest.mark.asyncio
async def test_ensure_binary_retries_after_transient_failure(tmp_path, monkeypatch):
    tool = "realesrgan"
    platform = sr._PLATFORM.get(sr.sys.platform) or ""
    data = _make_tool_zip(tool)
    monkeypatch.setattr(sr, "_TOOL_CHECKSUMS", {tool: {platform: hashlib.sha256(data).hexdigest()}})

    calls: list[int] = []

    async def fake_download(url):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("timeout")
        return data

    monkeypatch.setattr(sr, "_download_bytes", fake_download)
    assert await sr.ensure_binary(tool) is not None
    assert len(calls) == 2, "瞬时失败应重试一次"


@pytest.mark.asyncio
async def test_ensure_binary_degrades_when_checksum_mismatch(tmp_path, monkeypatch):
    tool = "waifu2x"
    platform = sr._PLATFORM.get(sr.sys.platform) or ""
    monkeypatch.setattr(sr, "_TOOL_CHECKSUMS", {tool: {platform: "0" * 64}})

    async def fake_download(url):
        return _make_tool_zip(tool)

    monkeypatch.setattr(sr, "_download_bytes", fake_download)
    assert await sr.ensure_binary(tool) is None, "校验不匹配必须降级，不能拿可疑二进制当工具"


@pytest.mark.asyncio
async def test_upscale_failure_reports_vulkan_unavailable(tmp_path, monkeypatch):
    """开关开着却没效果时，日志要给可读结论：无 Vulkan 设备是最常见原因。"""
    logs: list[str] = []
    monkeypatch.setattr(sr.signal, "show_log_text", logs.append)
    monkeypatch.setattr(sr, "ensure_binary", lambda tool: _pass())
    monkeypatch.setattr(sr, "_SR_SKIP_REPORTED", False, raising=False)

    class _FailProc:
        returncode = 1

        async def communicate(self):
            return b"", b"vkCreateInstance failed -9"

        def kill(self):
            pass

        async def wait(self):
            return 1

    async def fake_exec(*args, **kwargs):
        return _FailProc()

    monkeypatch.setattr(sr.asyncio, "create_subprocess_exec", fake_exec)

    src = tmp_path / "a.jpg"
    dst = tmp_path / "b.jpg"
    _make_jpg(src, 300, 450)

    assert await sr.upscale_image(src, dst, sr.SrPreset.REALESR_PHOTO_4X) is False
    hits = [text for text in logs if "海报超分未生效" in text]
    assert len(hits) == 1, f"结论行应只报一次，实际: {hits}"
    assert "Vulkan" in hits[0]
