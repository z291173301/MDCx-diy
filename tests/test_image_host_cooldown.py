"""议题 #21 回归：图床级失败冷却（分类阈值 + 持久化 + 恢复清零）。"""

from __future__ import annotations

import json

import pytest

from mdcx.config.resources import resources
from mdcx.core import image_host_cooldown as ihc

URL = "https://awsimgsrc.dmm.co.jp/pics_dig/mono/movie/cjod499/cjod499pl.jpg"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "u", lambda rel: tmp_path / rel)
    ihc._reset_for_test()
    yield
    ihc._reset_for_test()


def test_transport_errors_never_cool_down():
    """传输层失败=本地网络问题：连续 10 次也不得冷却图床。"""
    for _ in range(10):
        ihc.record_failure(URL, "请求失败: Connection reset by peer")
        ihc.record_failure(URL, "请求失败: TLS handshake EOF")
    assert ihc.remaining_seconds(URL) == 0


def test_server_errors_cool_after_threshold():
    for _ in range(ihc._FAIL_THRESHOLD - 1):
        ihc.record_failure(URL, "HTTP 429 too many requests")
    assert ihc.remaining_seconds(URL) == 0
    ihc.record_failure(URL, "HTTP 429 too many requests")
    assert ihc.remaining_seconds(URL) > 0


def test_404_and_unknown_not_counted():
    """404/失效属业务常态，unknown 不记账（与 404 不计错误率同族）。"""
    ihc.record_failure(URL, "HTTP 404")
    ihc.record_failure(URL, "HTTP 404 not found")
    ihc.record_failure(URL, "解析炸了")
    assert ihc.remaining_seconds(URL) == 0


def test_success_clears_window_and_cooldown():
    for _ in range(ihc._FAIL_THRESHOLD - 1):
        ihc.record_failure(URL, "HTTP 503")
    ihc.record_success(URL)
    ihc.record_failure(URL, "HTTP 503")
    assert ihc.remaining_seconds(URL) == 0, "成功清零后失败窗口应重新从 1 计"

    for _ in range(ihc._FAIL_THRESHOLD):
        ihc.record_failure(URL, "HTTP 503")
    assert ihc.remaining_seconds(URL) > 0
    ihc.record_success(URL)
    assert ihc.remaining_seconds(URL) == 0, "成功应同时解除已激活的冷却"


def test_cooldown_persists_across_restart(tmp_path):
    for _ in range(ihc._FAIL_THRESHOLD):
        ihc.record_failure(URL, "HTTP 503")
    assert (tmp_path / "image_host_cooldown.json").exists()

    ihc._reset_for_test()  # 模拟进程重启
    assert ihc.remaining_seconds(URL) > 0, "重启后冷却状态应从磁盘恢复"


def test_expired_entries_dropped_on_load(tmp_path):
    store = tmp_path / "image_host_cooldown.json"
    store.write_text(json.dumps({"awsimgsrc.dmm.co.jp": 0}), encoding="utf-8")
    ihc._reset_for_test()
    assert ihc.remaining_seconds(URL) == 0


def test_cloudflare_block_counts_as_server_error():
    ihc.record_failure(URL, "HTTP 403 被 CloudFlare 拦截")
    ihc.record_failure(URL, "HTTP 403 被 CloudFlare 拦截")
    ihc.record_failure(URL, "HTTP 403 被 CloudFlare 拦截")
    assert ihc.remaining_seconds(URL) > 0
