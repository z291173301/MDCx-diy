"""海报低清超分增强（议题 #26，借鉴 sqzw-x/amane sr 模块设计）。

实现选型（与 amane 一致）：调用 ncnn-vulkan 外部二进制而非打包 torch/ONNX——
- Real-ESRGAN（xinntao v0.2.5.0）与 waifu2x（nihui 20250915）官方 GitHub Release
  的三平台 zip，首次使用时按需下载到 userdata/sr/tools/，不随包分发；
- 模型目录随 zip 下发，二进制按自身所在目录解析模型（cwd 设为二进制目录）；
- 全程失败静默降级原图：无 Vulkan 环境/下载失败/超时无输出都不影响刮削结果。

触发策略：`poster_sr_enabled` 默认关；仅当海报最长边 < `poster_sr_max_dim`
（老图/下架图常见 147x200 形态）且开关开启时对落盘海报跑一次超分并原子替换。
"""

import asyncio
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from enum import StrEnum
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from ..config.manager import manager
from ..config.resources import resources
from ..signals import signal

_PRESET_REALESRGAN_MODEL = "realesrgan-x4plus"
_PRESET_WAIFU_MODEL = "upconv_7_photo"

_RELEASE_REALESRGAN = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0"
_RELEASE_WAIFU2X = "https://github.com/nihui/waifu2x-ncnn-vulkan/releases/download/20250915"

_PLATFORM = {"darwin": "macos", "linux": "linux", "win32": "windows"}


class SrPreset(StrEnum):
    REALESR_PHOTO_4X = "realesr-photo-4x"
    WAIFU_PHOTO_2X = "waifu-photo-2x"


_PRESET_META: dict[SrPreset, dict[str, object]] = {
    SrPreset.REALESR_PHOTO_4X: {
        "tool": "realesrgan",
        "model": _PRESET_REALESRGAN_MODEL,
        "scale": 4,
        "model_flag": "-n",
    },
    SrPreset.WAIFU_PHOTO_2X: {
        "tool": "waifu2x",
        "model": _PRESET_WAIFU_MODEL,
        "scale": 2,
        "model_flag": "-m",
    },
}

_TOOL_DOWNLOAD_URLS: dict[str, dict[str, str]] = {
    "realesrgan": {
        platform: f"{_RELEASE_REALESRGAN}/realesrgan-ncnn-vulkan-20220424-{platform}.zip"
        for platform in _PLATFORM.values()
    },
    "waifu2x": {
        platform: f"{_RELEASE_WAIFU2X}/waifu2x-ncnn-vulkan-20250915-{platform}.zip" for platform in _PLATFORM.values()
    },
}

# 输出尺寸/耗时护栏：低清海报超分正常 <60s；给足余量防卡死批刮任务
_SR_TIMEOUT_SECONDS = 300.0
_SR_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _tools_dir() -> Path:
    return resources.u("sr/tools")


def binary_path(tool: str) -> Path:
    name = f"{tool}.exe" if sys.platform == "win32" else tool
    return _tools_dir() / tool / name


def is_binary_ready(tool: str) -> bool:
    path = binary_path(tool)
    if not path.is_file():
        return False
    if sys.platform == "win32":
        return True
    return os.access(path, os.X_OK)


def _make_executable(path: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _extract_zip(data: bytes, dest_dir: Path) -> None:
    """ncnn-vulkan 包带一层顶层目录：解到父目录后整体改名到 dest_dir。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        top = names[0].split("/")[0] if names and "/" in names[0] else ""
        if top:
            with tempfile.TemporaryDirectory(dir=dest_dir.parent) as tmp:
                zf.extractall(tmp)
                extracted = Path(tmp) / top
                if extracted.is_dir():
                    if dest_dir.exists():
                        shutil.rmtree(dest_dir, ignore_errors=True)
                    shutil.copytree(extracted, dest_dir, dirs_exist_ok=True)
        else:
            zf.extractall(dest_dir)
    target = binary_path_from_dir(dest_dir)
    _make_executable(target)


def binary_path_from_dir(tool_dir: Path) -> Path:
    name = tool_dir.name
    return tool_dir / (f"{name}.exe" if sys.platform == "win32" else name)


async def ensure_binary(tool: str) -> Path | None:
    """返回可执行文件路径；平台不支持/下载失败返回 None（调用方降级）。"""
    path = binary_path(tool)
    if is_binary_ready(tool):
        return path
    platform = _PLATFORM.get(sys.platform)
    url = _TOOL_DOWNLOAD_URLS.get(tool, {}).get(platform or "")
    if not url:
        signal.show_traceback_log(f"🔶 超分：当前系统不支持自动下载（{sys.platform}）")
        return None
    try:
        signal.show_log_text(f"📥 首次使用，下载超分工具 {tool}（约 30-60MB，仅一次）...")
        proxy = getattr(manager.config, "proxy", "") or ""
        async with httpx.AsyncClient(timeout=httpx.Timeout(600), follow_redirects=True, proxy=proxy or None) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _extract_zip(resp.content, path.parent)
    except Exception as e:
        signal.show_log_text(f"🔴 超分工具下载失败（海报保持原图）: {e}")
        return None
    if not is_binary_ready(tool):
        signal.show_log_text("🔴 超分工具解压后不可执行（海报保持原图）")
        return None
    signal.show_log_text(f"✅ 超分工具就绪: {path}")
    return path


def build_args(src: Path, dst: Path, preset: SrPreset) -> list[str]:
    meta = _PRESET_META[preset]
    cli = ["-i", str(src.absolute()), "-o", str(dst.absolute())]
    cli += [str(meta["model_flag"]), str(meta["model"])]
    cli += ["-s", str(meta["scale"]), "-f", "jpg"]
    return cli


async def upscale_image(src: Path, dst: Path, preset: SrPreset) -> bool:
    """超分单图。任何失败返回 False，不抛出。"""
    tool = str(_PRESET_META[preset]["tool"])
    exe = await ensure_binary(tool)
    if exe is None:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe),
            *build_args(src, dst, preset),
            cwd=str(exe.parent),  # realesrgan 按 CWD 解析模型路径
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SR_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            signal.show_traceback_log(f"🔶 超分超时（保持原图）: {src.name}")
            return False
        if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            signal.show_traceback_log(f"🔶 超分失败（保持原图）: {src.name} {stderr.decode(errors='replace')[:120]}")
            return False
        return True
    except Exception as e:
        signal.show_traceback_log(f"🔶 超分异常（保持原图）: {src.name}: {e}")
        return False


def _image_max_dim(path: Path) -> int:
    try:
        with Image.open(path) as img:
            return max(img.size)
    except Exception:
        return 0


def _sr_trigger_ok(path: Path) -> bool:
    """开关 + 尺寸 + 体积护栏。最长边小于阈值（默认 1200）才值得超分。"""
    cfg = manager.config
    if not getattr(cfg, "poster_sr_enabled", False):
        return False
    if not path.is_file():
        return False
    try:
        if path.stat().st_size > _SR_MAX_INPUT_BYTES:
            return False
    except OSError:
        return False
    max_dim = _image_max_dim(path)
    return 0 < max_dim < int(getattr(cfg, "poster_sr_max_dim", 1200))


async def maybe_upscale_poster(poster_path: Path) -> bool:
    """刮削收尾钩子：符合条件时对海报超分并原子替换；否则原样返回 False。"""
    if not _sr_trigger_ok(poster_path):
        return False
    preset_raw = str(getattr(manager.config, "poster_sr_preset", SrPreset.REALESR_PHOTO_4X))
    try:
        preset = SrPreset(preset_raw)
    except ValueError:
        preset = SrPreset.REALESR_PHOTO_4X
    tmp = poster_path.with_name(poster_path.stem + ".[SR].jpg")
    try:
        if not await upscale_image(poster_path, tmp, preset):
            return False
        # 校验产物确为放大图，防二进制输出异常覆盖原图
        if _image_max_dim(tmp) <= _image_max_dim(poster_path):
            signal.show_traceback_log(f"🔶 超分产物尺寸未增大，弃用: {poster_path.name}")
            return False
        os.replace(tmp, poster_path)
        signal.show_log_text(f"✨ 海报超分完成: {poster_path.name}")
        return True
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
