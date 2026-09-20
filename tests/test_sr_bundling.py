"""构建期超分工具拉取与内嵌回归（全程离线）：校验门禁、幂等、按平台内嵌。"""

from __future__ import annotations

import hashlib
import os
import sys
import zipfile
from io import BytesIO

import pytest

from mdcx.config.resources import resources
from mdcx.core import super_resolution as sr
from scripts import build as build_mod
from scripts import fetch_sr_tools as fetch


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from mdcx.config.manager import manager

    monkeypatch.setattr(resources, "u", lambda rel: tmp_path / rel)
    monkeypatch.setattr(manager.config, "poster_sr_enabled", True)
    yield


def _make_tool_zip(tool: str) -> bytes:
    """构造官方形态的 zip：顶层目录 `<tool>/`，二进制名为 `<tool>-ncnn-vulkan`。"""
    name = f"{tool}-ncnn-vulkan.exe" if sys.platform == "win32" else f"{tool}-ncnn-vulkan"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{tool}/{name}", b"fake binary")
        zf.writestr(f"{tool}/models/dummy.param", b"dummy")
    return buffer.getvalue()


def test_fetch_tool_verifies_checksum_and_extracts(tmp_path, monkeypatch):
    tool = "realesrgan"
    platform = sr._PLATFORM.get(sys.platform) or ""
    data = _make_tool_zip(tool)
    monkeypatch.setattr(fetch, "_TOOL_CHECKSUMS", {tool: {platform: hashlib.sha256(data).hexdigest()}})
    monkeypatch.setattr(fetch, "_download", lambda url: data)

    result = fetch.fetch_tool(tool, platform, tmp_path / "sr_tools")
    assert result.startswith("就绪")
    assert fetch.is_ready(tmp_path / "sr_tools", tool)


def test_fetch_tool_rejects_checksum_mismatch(tmp_path, monkeypatch):
    tool = "waifu2x"
    platform = sr._PLATFORM.get(sys.platform) or ""
    monkeypatch.setattr(fetch, "_TOOL_CHECKSUMS", {tool: {platform: "1" * 64}})
    monkeypatch.setattr(fetch, "_download", lambda url: _make_tool_zip(tool))

    with pytest.raises(RuntimeError, match="sha256"):
        fetch.fetch_tool(tool, platform, tmp_path / "sr_tools")


def test_fetch_tool_skips_download_when_ready(tmp_path, monkeypatch):
    tool = "realesrgan"
    target = tmp_path / "sr_tools" / tool
    target.mkdir(parents=True)
    binary = target / ("realesrgan-ncnn-vulkan.exe" if sys.platform == "win32" else "realesrgan-ncnn-vulkan")
    binary.write_text("#!/bin/sh\nexit 0")
    binary.chmod(0o755)

    def fail(url):
        raise AssertionError("已就绪时不应发起下载")

    monkeypatch.setattr(fetch, "_download", fail)
    assert "跳过" in fetch.fetch_tool(tool, sr._PLATFORM.get(sys.platform) or "", tmp_path / "sr_tools")


@pytest.mark.asyncio
async def test_bundled_layout_matches_runtime_lookup(tmp_path, monkeypatch):
    """打包资源目录 `sr_tools/<tool>` 必须与运行时 `_MEIPASS/sr_tools/<tool>` 查找路径一致，
    且内置工具要释放到 userdata 缓存目录后使用（打包解压目录不保证可写）。"""
    data_root = tmp_path / "data"
    monkeypatch.setattr(resources, "u", lambda rel: data_root / rel)
    mei = tmp_path / "mei" / "sr_tools" / "realesrgan"
    mei.mkdir(parents=True)
    (mei / "realesrgan-ncnn-vulkan").write_text("#!/bin/sh\nexit 0")
    monkeypatch.setattr(sr.sys, "_MEIPASS", str(tmp_path / "mei"), raising=False)

    assert sr._builtin_dir("realesrgan") == mei
    resolved = await sr.ensure_binary("realesrgan")
    assert resolved is not None and resolved.is_file()
    assert resolved == sr.binary_path("realesrgan")


def test_build_args_include_sr_tools_only_for_windows_and_linux(tmp_path, monkeypatch):
    """一体包资源只进 Windows/Linux 包；macOS 不内嵌（签名/公证与 Gatekeeper 风险）。"""
    root = tmp_path / "sr_tools" / "realesrgan"
    root.mkdir(parents=True)
    (root / "realesrgan").write_text("#!/bin/sh\nexit 0")
    monkeypatch.setattr(build_mod, "SR_TOOLS_DIR", str(tmp_path / "sr_tools"))

    for system, expected in (("Windows", True), ("Linux", True), ("Darwin", False)):
        monkeypatch.setattr(build_mod.platform, "system", lambda s=system: s)
        manager = build_mod.BuildManager("MDCx", "20260920", create_dmg=False, debug=True)
        args = manager._sr_tools_binary_args()
        marker = f"{os.pathsep}sr_tools/"
        assert any(marker in item for item in args) is expected, f"{system}: {args}"
