"""图床级失败冷却（议题 #21，借鉴 mdcz ImageHostCooldownTracker + PersistentCooldownStore 设计）。

失败分类纪律（与「404 不计错误率」「真实限流信号只有 403/429/连接异常」同族）：
- 传输层失败（超时/连接重置/TLS/DNS/代理）= 本地网络问题，**不计**图床失败；
- 服务器响应类错误（403/408/429/5xx/CF 拦截）= 图床侧问题，滑动窗口内连续达到
  阈值即冷却该 host，期内候选直接跳过，省掉对死图床的重复请求；
- 404/失效属业务常态（未收录），两类都不计。

冷却状态落盘（userdata/image_host_cooldown.json，原子写），跨进程重启保留——
批刮中途重启/断点续刮不再重撞同一图床。
"""

import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..config.resources import resources
from ..signals import signal

# 滑动窗口内连续 N 次服务器侧失败触发冷却
_FAIL_WINDOW_SECONDS = 60.0
_FAIL_THRESHOLD = 3
_COOLDOWN_SECONDS = 120.0

# 传输层错误特征：本地/链路问题，不计图床失败
_TRANSPORT_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "reset by peer",
    "connection refused",
    "tls",
    "ssl",
    "dns",
    "eof occurred",
    "proxy",
    "socks",
    "network is unreachable",
    "econn",
)
# 服务器侧错误特征：计入图床失败
_SERVER_MARKERS = (
    "http 403",
    "http 408",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "cloudflare",
    "cf_chl",
    "forbidden",
    "too many requests",
    "rate limit",
    "拦截",
)

_state_lock = threading.Lock()
_host_failures: dict[str, list[float]] = {}
_cooldown_until: dict[str, float] = {}
_loaded = False


def _store_path() -> Path:
    return resources.u("image_host_cooldown.json")


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _classify(error_text: str) -> str:
    text = error_text.lower()
    if any(marker in text for marker in _SERVER_MARKERS):
        return "server"
    if any(marker in text for marker in _TRANSPORT_MARKERS):
        return "transport"
    return "other"


def _persist_locked() -> None:
    """原子写（tmp + os.replace，持久化快照三性质之一）。"""
    now = time.monotonic()
    wall = time.time()
    data = {host: round(wall + (until - now)) for host, until in _cooldown_until.items() if until > now}
    path = _store_path()
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        signal.show_traceback_log(f"🔶 图床冷却状态保存失败: {e}")


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _store_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    now_wall = time.time()
    now_mono = time.monotonic()
    for host, until_wall in raw.items():
        try:
            remaining = float(until_wall) - now_wall
        except (TypeError, ValueError):
            continue
        # 上限放宽 2s：持久化 round() 有进位误差，过期的下一轮 load 自然丢弃
        if 0 < remaining <= _COOLDOWN_SECONDS + 2:
            _cooldown_until[str(host).lower()] = now_mono + remaining


def record_failure(url: str, error_text: str) -> None:
    """记录一次图片下载失败；仅服务器侧错误计入，窗口内达阈值进入冷却。"""
    if not url or not error_text:
        return
    if _classify(error_text) != "server":
        return
    host = _host_of(url)
    if not host:
        return
    now = time.monotonic()
    with _state_lock:
        _load()
        fails = [t for t in _host_failures.get(host, []) if now - t <= _FAIL_WINDOW_SECONDS]
        fails.append(now)
        was_cooling = _cooldown_until.get(host, 0.0) > now
        if len(fails) >= _FAIL_THRESHOLD:
            _host_failures.pop(host, None)
            _cooldown_until[host] = now + _COOLDOWN_SECONDS
            if not was_cooling:
                signal.show_traceback_log(
                    f"🕒 图床连续 {_FAIL_THRESHOLD} 次服务器侧失败，冷却 {host} {_COOLDOWN_SECONDS:.0f}s"
                )
            _persist_locked()
        else:
            _host_failures[host] = fails


def record_success(url: str) -> None:
    """成功即清零该 host 的失败窗口与冷却（图床恢复）。"""
    host = _host_of(url) if url else ""
    if not host:
        return
    with _state_lock:
        _load()
        # 两个 pop 都必须执行——or 短路会让冷却漏清零（多源判定禁 or 短路同族教训）
        had_fails = _host_failures.pop(host, None) is not None
        had_cooldown = _cooldown_until.pop(host, None) is not None
        if had_fails or had_cooldown:
            _persist_locked()


def remaining_seconds(url: str) -> float:
    """该 URL 所属图床的剩余冷却秒数；不在冷却中返回 0。"""
    host = _host_of(url) if url else ""
    if not host:
        return 0.0
    with _state_lock:
        _load()
        until = _cooldown_until.get(host, 0.0)
        return max(0.0, until - time.monotonic())


def _reset_for_test() -> None:
    global _loaded
    with _state_lock:
        _host_failures.clear()
        _cooldown_until.clear()
        _loaded = False
