"""构建期拉取超分工具（Windows/Linux 一体包内嵌；macOS 不调用）。

从官方 GitHub Release 下载 realesrgan / waifu2x 的 ncnn-vulkan 预编译包，
比对 `mdcx.core.super_resolution._TOOL_CHECKSUMS` 基准后解压到统一目录，供
打包（`--add-binary`）与 CI 缓存复用。工具二进制不入库，只在构建期获取；
sha256 不匹配一律失败，避免坏包被打进发布产物。

用法:
    uv run python -m scripts.fetch_sr_tools --dest build/sr_tools
    uv run python -m scripts.fetch_sr_tools --tool realesrgan --platform windows
    uv run python -m scripts.fetch_sr_tools --force
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

from mdcx.core.super_resolution import (
    _PLATFORM,
    _TOOL_CHECKSUMS,
    _TOOL_DOWNLOAD_URLS,
    _make_executable,
    binary_path_from_dir,
)

CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 120.0

# 与打包参数 `sr_tools/<tool>` 保持一致，运行时按 `sys._MEIPASS / "sr_tools" / tool` 查找
DEFAULT_DEST = "build/sr_tools"


def tool_dir(dest: Path, tool: str) -> Path:
    return dest / tool


def is_ready(dest: Path, tool: str) -> bool:
    """缓存已就绪时跳过下载（CI 缓存恢复的快路径）。"""
    path = binary_path_from_dir(tool, tool_dir(dest, tool))
    if not path.is_file():
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def _extract(data: bytes, dest: Path) -> None:
    """官方包带一层顶层目录：解到临时目录后整体搬到 `<dest>/<tool>`。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        top = names[0].split("/")[0] if names and "/" in names[0] else ""
        tmp = dest.parent / f".{dest.name}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        zf.extractall(tmp)
        extracted = tmp / top if top else tmp
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(extracted, dest)
        shutil.rmtree(tmp, ignore_errors=True)
    _make_executable(binary_path_from_dir(dest.name, dest))


def _download(url: str) -> bytes:
    """下载官方 release 资产；HTTP 4xx/5xx 错误串带截断响应体，便于排障。"""
    with httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT), follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        preview = " ".join(resp.text.split())[:120]
        raise RuntimeError(f"HTTP {resp.status_code}: {preview}")
    return resp.content


def fetch_tool(tool: str, platform: str, dest: Path, force: bool = False) -> str:
    url = _TOOL_DOWNLOAD_URLS[tool][platform]
    target = tool_dir(dest, tool)
    if not force and is_ready(dest, tool):
        return f"跳过 {tool}: 已就绪 {target}"
    data = _download(url)
    expected = _TOOL_CHECKSUMS.get(tool, {}).get(platform, "")
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise RuntimeError(f"sha256 不匹配 {tool}/{platform}: 期望 {expected[:12]}… 实际 {actual[:12]}…")
    if not expected:
        print(f"⚠️ {tool}/{platform} 无基准 sha256，跳过校验并解压（建议补进 _TOOL_CHECKSUMS）")
    _extract(data, target)
    if not is_ready(dest, tool):
        raise RuntimeError(f"解压产物不可执行: {target}")
    return f"就绪 {tool}/{platform}: {len(data) / 1048576:.2f} MB <- {url} (sha256 {actual[:12]}…)"


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取超分工具（构建期使用，不入 git）")
    parser.add_argument("--tool", choices=["realesrgan", "waifu2x", "all"], default="all")
    parser.add_argument("--platform", choices=[*_PLATFORM.values(), "current"], default="current")
    parser.add_argument("--dest", default=DEFAULT_DEST, help=f"解压目标目录（默认 {DEFAULT_DEST}）")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，强制重新下载")
    args = parser.parse_args()

    platform = _PLATFORM.get(sys.platform) if args.platform == "current" else args.platform
    if not platform:
        print(f"❌ 当前系统不支持自动下载: {sys.platform}")
        return 1
    tools = list(_TOOL_DOWNLOAD_URLS) if args.tool == "all" else [args.tool]
    dest = Path(args.dest)

    failed = False
    for tool in tools:
        try:
            print(fetch_tool(tool, platform, dest, args.force))
        except Exception as e:
            failed = True
            print(f"❌ {tool}/{platform} 拉取失败: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
