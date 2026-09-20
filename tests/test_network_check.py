import sys
import threading
from types import SimpleNamespace

import pytest

from mdcx.config.enums import Website
from mdcx.core import network_check as nc
from mdcx.core.network_check import (
    NetworkCheckResult,
    NetworkCheckSpec,
    NetworkCheckStatus,
    _classify_http_result,
    _compute_used_proxy,
    _is_cloudflare_challenge,
    _probe_crawler_capability,
    _site_result_level,
    build_network_check_specs,
    format_result_line,
    load_site_check_cache,
    merge_site_check_cache,
    run_network_check,
    run_network_check_item,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", url: str = "https://example.test"):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = {}
        self.encoding = "utf-8"


class FakeClient:
    def __init__(self, *, fail_url_part: str = ""):
        self.fail_url_part = fail_url_part
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail_url_part and self.fail_url_part in url:
            raise RuntimeError("boom")
        return FakeResponse(url=url), ""


class FakeBypassClient:
    def __init__(self, *, bypass_ok: bool = True):
        self.bypass_ok = bypass_ok
        self.calls: list[dict] = []
        self.bypass_calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(
            text="<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script>Cloudflare</html>",
            url=url,
        ), ""

    async def _try_bypass_cloudflare(self, **kwargs):
        self.bypass_calls.append(kwargs)
        if not self.bypass_ok:
            return None, "bypass failed"
        response = FakeResponse(text="<html>ok</html>", url=kwargs["target_url"])
        response.headers["x-mdcx-bypass-mode"] = "mirror"
        return response, ""


class FakeConfig:
    use_proxy = False
    proxy = ""
    cf_bypass_url = ""
    cf_bypass_proxy = ""
    cf_bypass_trawl_url = ""
    cf_bypass_trawl_backend = "trawl"
    timeout = 5
    javdb = ""
    javbus = ""
    theporndb_api_token = ""
    proxy_sites = ""
    direct_sites = ""

    def proxy_hosts_list(self):
        return [s.strip() for s in (self.proxy_sites or "").split(",") if s.strip()]

    def direct_sites_list(self):
        return [s.strip() for s in (self.direct_sites or "").split(",") if s.strip()]

    def get_site_url(self, site, default=""):
        return default


class FakeManager:
    config = FakeConfig()
    computed = None


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fake_manager(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: FakeManager())


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_7mmtv_in_check_specs_and_probe_path(monkeypatch: pytest.MonkeyPatch):
    """7mmtv 已注册即自动进入检测清单；搜索探测链路走 _generate_search_url→搜索页匹配。"""

    from mdcx.config.models import Website as WebsiteEnum
    from mdcx.crawlers import get_registered_crawler_sites

    # 1) 注册爬虫清单含 7mmtv → 检测规格构建自动覆盖
    values = [s.value for s in get_registered_crawler_sites()]
    assert "7mmtv" in values

    # 2) 探测路径：搜索页 fixture 匹配探针番号 → OK
    from mdcx.core.network_check import (
        NetworkCheckSpec,
        NetworkCheckStatus,
        _probe_crawler_capability,
    )

    class FakeResponse:
        text = '<figure class="video-preview"><a href="/zh/x/SSNI-647.html"><img alt="SSNI-647 標題"></a></figure>'
        encoding = "utf-8"

    class FakeClient:
        async def request(self, method, url, **kwargs):
            return FakeResponse(), ""

    spec = NetworkCheckSpec(
        name="7mmtv",
        group="刮削站点",
        url="https://www.7mmtv.sx",
        site=WebsiteEnum.MMTV,
    )
    status, message = await _probe_crawler_capability(FakeClient(), spec)
    assert status is NetworkCheckStatus.OK, f"7mmtv 探测失败: {status} {message}"

    # 3) 搜索页无匹配番号时给 WARNING（未被收录提示），不炸整轮
    class FakeResponseMiss(FakeResponse):
        text = '<figure class="video-preview"><a href="/x.html"><img alt="OTHER-1"></a></figure>'

    class FakeClientMiss:
        async def request(self, method, url, **kwargs):
            return FakeResponseMiss(), ""

    # get_real_url 无匹配时 _parse_search_page 抛 CrawlerException → 转 WARNING
    status2, message2 = await _probe_crawler_capability(FakeClientMiss(), spec)
    assert status2 is NetworkCheckStatus.WARNING, f"未收录场景应 WARNING: {status2} {message2}"


@pytest.mark.anyio
async def test_build_network_check_specs_uses_registered_sites_without_key_error(monkeypatch: pytest.MonkeyPatch):
    class DynamicCrawler:
        @classmethod
        def base_url_(cls):
            return ""

    class CustomConfig(FakeConfig):
        def get_site_url(self, site, default=""):
            return "https://custom.example"

    class CustomManager:
        config = CustomConfig()
        computed = None

    fake_crawlers = SimpleNamespace(
        get_registered_crawler_sites=lambda include_hidden=False: [Website.OFFICIAL],
        get_crawler=lambda site: DynamicCrawler,
    )
    monkeypatch.setitem(sys.modules, "mdcx.crawlers", fake_crawlers)
    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: CustomManager())

    specs = await build_network_check_specs()

    assert any(spec.site == Website.OFFICIAL and spec.url == "https://custom.example" for spec in specs)


@pytest.mark.anyio
async def test_probe_crawler_strips_special_check_path_from_base_url(monkeypatch: pytest.MonkeyPatch):
    """议题 #77：spec.url 拼有 SPECIAL_CHECK_PATHS 探测路径时，探测构造爬虫必须剥回主站根 URL。

    实证：airav_cc 探测 URL 出现 https://airav.io/playon.aspx?hid=44733/cn/search_result?kw=... 畸形拼接；
    javdb（/v/D16Q5?locale=zh）、javbus（/FSDSS-660）同因导致「搜索失败/未匹配到番号」。
    """
    import mdcx.crawlers as crawlers_mod

    recorded: dict = {}

    class OriginCrawler:
        def __init__(self, client, base_url="", browser=None):
            self.base_url = base_url
            recorded["base_url"] = base_url

        def new_context(self, input_data):
            return SimpleNamespace(input=input_data)

        async def _generate_search_url(self, ctx):
            return f"{self.base_url}/search?q=TEST"

        async def _parse_search_page(self, ctx, html, search_url):
            return ["https://example.com/detail"]

        def _get_headers(self, ctx):
            return None

        def _get_cookies(self, ctx):
            return None

    monkeypatch.setattr(crawlers_mod, "get_crawler", lambda site: OriginCrawler)

    client = FakeClient()
    spec = NetworkCheckSpec(
        name="javbus", group="刮削站点", url="https://www.javbus.com/FSDSS-660", site=Website.JAVBUS
    )

    status, _ = await _probe_crawler_capability(client, spec)

    assert status is NetworkCheckStatus.OK
    assert recorded["base_url"] == "https://www.javbus.com"
    assert client.calls[0]["url"] == "https://www.javbus.com/search?q=TEST"


@pytest.mark.anyio
async def test_mirror_sample_spec_skips_scrape_probe(monkeypatch: pytest.MonkeyPatch):
    """议题 #77：镜像抽样项（如 xcity·镜像）只验证连通性，不做刮削探测。

    实证：xcity.jp 是展示页域名、/api/search 不存在（实测 404；API 只在 tc.xcity.jp），
    探测按主站 URL 模式打到镜像域名必出「搜索页请求失败」误报。
    镜像抽样用途是让主站挂时看到镜像是否可用，连通即达标。
    """
    import mdcx.crawlers as crawlers_mod

    probe_called = []

    class ShouldNotBeCalled:
        def __init__(self, client, base_url="", browser=None):
            probe_called.append(True)

    monkeypatch.setattr(crawlers_mod, "get_crawler", lambda site: ShouldNotBeCalled)

    spec = NetworkCheckSpec(name="xcity·镜像", group="刮削站点", url="https://xcity.jp", site=Website.XCITY)
    result = await run_network_check_item(spec, client=FakeClient())

    assert result.status == NetworkCheckStatus.OK
    assert result.message == "连接正常"
    assert not probe_called, "镜像抽样不应触发刮削探测"


@pytest.mark.anyio
async def test_run_network_check_item_retries_probe_within_first_round(monkeypatch: pytest.MonkeyPatch):
    """议题 #118：首轮检测内部就自动递进重试（30s/45s/60s），用户不必手动点「重试失败项」。"""
    seen: list[float | None] = []

    async def fake(client, spec, probe_timeout=None):
        seen.append(probe_timeout)
        return NetworkCheckStatus.WARNING, "站点可达但刮削探测超时"

    monkeypatch.setattr(nc, "_probe_crawler_capability", fake)
    lines: list[str] = []
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    result = await run_network_check_item(spec, client=FakeClient(), progress=lines.append)

    assert seen == [30.0, 45.0, 60.0], "单项检测必须走满轮内阶梯"
    assert result.status == NetworkCheckStatus.WARNING
    assert "3 次均超时" in result.message
    assert any("avbase 第 2/3 次刮削探测" in line for line in lines), lines


def test_message_for_error_tls_handshake():
    """SSL_ERROR_SYSCALL / UNEXPECTED_EOF 类的 TLS 握手中断要给出可执行指引（议题 #77 javdb_api/r18dev）。"""
    from mdcx.core.network_check import _message_for_error

    msg = _message_for_error("SSL_ERROR_SYSCALL; error queue empty: Failed to perform")
    assert "TLS" in msg or "SSL" in msg
    assert "节点" in msg

    msg2 = _message_for_error("UNEXPECTED_EOF_WHILE_READING")
    assert "TLS" in msg2 or "SSL" in msg2


def test_classify_403_cf_challenge_hint():
    """CF 403 要引导配 CF Bypass（议题 #77 lulubar/missav 批）。"""
    cf_body = "<html><title>Attention Required! | Cloudflare</title><body>cloudflare ray id</body></html>"

    spec = NetworkCheckSpec(name="missav", group="刮削站点", url="https://missav.ai")
    status, message = _classify_http_result(spec, 403, cf_body)

    assert status == NetworkCheckStatus.WARNING
    assert "Cloudflare" in message
    assert "CF Bypass" in message or "外部 CF 服务" in message


def test_classify_403_plain_block():
    """非 CF 的 403（出站 IP 被站点封）提示换节点，不提 CF Bypass。"""
    spec = NetworkCheckSpec(name="getchu", group="刮削站点", url="http://www.getchu.com")
    status, message = _classify_http_result(spec, 403, "<html>Forbidden</html>")

    assert status == NetworkCheckStatus.WARNING
    assert "节点" in message
    assert "CF Bypass" not in message


def test_format_summary_groups_failure_causes():
    """失败/警告项按成因分组显示在总结里（议题 #77）。"""
    from mdcx.core.network_check import NetworkCheckResult, format_summary

    def r(name, status, message):
        return NetworkCheckResult(
            spec=NetworkCheckSpec(name=name, group="刮削站点", url="https://x.test"),
            status=status,
            message=message,
        )

    results = [
        r("missav", NetworkCheckStatus.WARNING, "站点可达但搜索页被 Cloudflare 拦截"),
        r("getchu", NetworkCheckStatus.FAILED, "HTTP 403 请求被拒绝：当前节点出口 IP 可能被站点封禁"),
        r("javdb_api", NetworkCheckStatus.FAILED, "TLS 握手中断"),
        r("avbase", NetworkCheckStatus.WARNING, "站点可达但刮削探测 3 次均超时（30s/45s/60s），判定该站刮削探测无效"),
        r("ok", NetworkCheckStatus.OK, "连接正常"),
    ]
    lines = format_summary(results, elapsed=5.0, cancelled=False)

    text = "\n".join(lines)
    assert "根因分组" in text
    assert "Cloudflare" in text
    assert "节点" in text
    # 议题 #118：轮内重试仍超时的站点要单独成组，不能混进「其他异常」或「未收录」
    assert "刮削探测多次超时 ×1" in text
    assert "30s/45s/60s" in text


@pytest.mark.anyio
async def test_run_network_check_item_catches_single_item_exception():
    spec = NetworkCheckSpec(name="bad", group="刮削站点", url="https://bad.example")

    result = await run_network_check_item(spec, client=FakeClient(fail_url_part="bad"))

    assert result.status == NetworkCheckStatus.FAILED
    assert result.message == "检测异常"
    assert result.error == "boom"


@pytest.mark.anyio
async def test_run_network_check_survives_task_level_exception(monkeypatch: pytest.MonkeyPatch):
    """task.result() 层逃逸的异常（绕过 item 兜底）不应中断整轮检测（议题 #73）。

    实证：检测网络页跑到中途输出「网络检测出现异常：」（空消息）后整轮停止——
    主循环 task.result() 无兜底，单个 task 逃逸的异常中断整轮。空消息异常
    （如裸 TimeoutError，str() 为空）还导致用户看不到任何线索。
    """
    import mdcx.core.network_check as nc_module

    async def fake_specs():
        return [
            NetworkCheckSpec(name="good", group="基础连通性", url="https://good.example"),
            NetworkCheckSpec(name="boom", group="基础连通性", url="https://boom.example"),
            NetworkCheckSpec(name="also_good", group="基础连通性", url="https://also.example"),
        ]

    monkeypatch.setattr("mdcx.core.network_check.build_network_check_specs", fake_specs)

    real_item = nc_module.run_network_check_item

    async def flaky_item(spec, *, cancel_event=None, client=None, progress=None):
        if spec.name == "boom":
            raise TimeoutError  # 空消息异常，复刻用户场景的形态
        return await real_item(spec, cancel_event=cancel_event, client=client, progress=progress)

    monkeypatch.setattr("mdcx.core.network_check.run_network_check_item", flaky_item)
    lines: list[str] = []

    results = await run_network_check(progress=lines.append, client=FakeClient(), concurrency=3, emit_header=False)

    assert len(results) == 3
    by_name = {r.spec.name: r for r in results}
    assert by_name["good"].status == NetworkCheckStatus.OK
    assert by_name["also_good"].status == NetworkCheckStatus.OK
    assert by_name["boom"].status == NetworkCheckStatus.FAILED
    # 空消息异常也必须带上类型名，否则用户又拿到「出现异常：」空行
    assert "TimeoutError" in by_name["boom"].error
    assert any("网络检测已完成" in line for line in lines)


@pytest.mark.anyio
async def test_run_network_check_cancelled_task_does_not_stop(monkeypatch: pytest.MonkeyPatch):
    """单个 task 内部抛 CancelledError 应软着陆（记 CANCELLED），不中断整轮（议题 #73）。

    用户截图实锤：CancelledError 从 future.result() 逃逸导致整轮停止。
    整轮取消由 cancel_event 分支统一处理，单项内部取消不应穿透。
    """
    import asyncio

    async def fake_specs():
        return [
            NetworkCheckSpec(name="cancelled", group="基础连通性", url="https://c.example"),
            NetworkCheckSpec(name="good", group="基础连通性", url="https://g.example"),
        ]

    monkeypatch.setattr("mdcx.core.network_check.build_network_check_specs", fake_specs)

    real_item = nc.run_network_check_item

    async def flaky_item(spec, *, cancel_event=None, client=None, progress=None):
        if spec.name == "cancelled":
            raise asyncio.CancelledError
        return await real_item(spec, cancel_event=cancel_event, client=client, progress=progress)

    monkeypatch.setattr("mdcx.core.network_check.run_network_check_item", flaky_item)
    lines: list[str] = []

    results = await run_network_check(progress=lines.append, client=FakeClient(), emit_header=False)

    assert len(results) == 2
    by_name = {r.spec.name: r for r in results}
    assert by_name["cancelled"].status == NetworkCheckStatus.CANCELLED
    assert by_name["good"].status == NetworkCheckStatus.OK
    assert any("网络检测已完成" in line for line in lines)


@pytest.mark.anyio
async def test_run_network_check_does_not_stop_on_single_item_exception(monkeypatch: pytest.MonkeyPatch):
    async def fake_specs():
        return [
            NetworkCheckSpec(name="good", group="基础连通性", url="https://good.example"),
            NetworkCheckSpec(name="bad", group="基础连通性", url="https://bad.example"),
        ]

    monkeypatch.setattr("mdcx.core.network_check.build_network_check_specs", fake_specs)
    lines: list[str] = []

    results = await run_network_check(
        progress=lines.append, client=FakeClient(fail_url_part="bad"), concurrency=2, emit_header=False
    )

    assert len(results) == 2
    assert {result.spec.name: result.status for result in results} == {
        "good": NetworkCheckStatus.OK,
        "bad": NetworkCheckStatus.FAILED,
    }
    assert any("网络检测已完成" in line for line in lines)


@pytest.mark.anyio
async def test_run_network_check_can_cancel_between_groups(monkeypatch: pytest.MonkeyPatch):
    async def fake_specs():
        return [
            NetworkCheckSpec(name="first", group="基础连通性", url="https://first.example"),
            NetworkCheckSpec(name="second", group="刮削站点", url="https://second.example"),
        ]

    monkeypatch.setattr("mdcx.core.network_check.build_network_check_specs", fake_specs)
    cancel_event = threading.Event()
    lines: list[str] = []

    def progress(line: str):
        lines.append(line)
        if "first" in line:
            cancel_event.set()

    results = await run_network_check(
        progress=progress, cancel_event=cancel_event, client=FakeClient(), concurrency=1, emit_header=False
    )

    assert [result.spec.name for result in results] == ["first"]
    assert any("网络检测已取消" in line for line in lines)


@pytest.mark.anyio
async def test_dmm_api_spec_uses_real_query_url(monkeypatch: pytest.MonkeyPatch):
    class DmmApiCrawlerStub:
        @classmethod
        def base_url_(cls):
            return "https://api.dmm.com"

        @classmethod
        def _build_api_url(cls, **params):
            from urllib.parse import urlencode

            query = urlencode({"api_id": "test", "affiliate_id": "test", "output": "json", **params})
            return f"https://api.dmm.com/affiliate/v3/ItemList?{query}"

    fake_crawlers = SimpleNamespace(
        get_registered_crawler_sites=lambda include_hidden=False: [Website.DMM_API],
        get_crawler=lambda site: DmmApiCrawlerStub,
    )
    monkeypatch.setitem(sys.modules, "mdcx.crawlers", fake_crawlers)

    specs = await build_network_check_specs()

    dmm_api = next(spec for spec in specs if spec.site == Website.DMM_API)
    assert "api.dmm.com/affiliate/v3/ItemList" in dmm_api.url
    # v3 ItemList 必需参数缺失会 400 BAD REQUEST，keyword 用厂牌词验证搜索能力
    assert "site=FANZA" in dmm_api.url
    assert "service=digital" in dmm_api.url
    assert "floor=videoa" in dmm_api.url
    assert "keyword=SSIS" in dmm_api.url
    assert "keyword=SSIS-" not in dmm_api.url
    assert dmm_api.validator == "dmm_api"


@pytest.mark.anyio
async def test_missav_api_spec_uses_post_search(monkeypatch: pytest.MonkeyPatch):
    """Recombee search 端点只接受 POST（GET 405），检测须用真实搜索路径."""

    class MissavApiCrawlerStub:
        RECOMBEE_HOST = "client-rapi-missav.recombee.com"

        @classmethod
        def base_url_(cls):
            return "https://missav.ws"

        @classmethod
        def _sign_path(cls, path: str) -> str:
            return f"/missav-default{path}?frontend_timestamp=1&frontend_sign=sig"

    fake_crawlers = SimpleNamespace(
        get_registered_crawler_sites=lambda include_hidden=False: [Website.MISSAV_API],
        get_crawler=lambda site: MissavApiCrawlerStub,
    )
    monkeypatch.setitem(sys.modules, "mdcx.crawlers", fake_crawlers)

    specs = await build_network_check_specs()

    missav_api = next(spec for spec in specs if spec.site == Website.MISSAV_API)
    assert missav_api.method == "POST"
    assert "/search/users/anonymous/items/" in missav_api.url
    assert missav_api.json_data is not None and missav_api.json_data["searchQuery"] == "ssni-647"
    assert missav_api.validator == "missav_api"


@pytest.mark.anyio
async def test_run_network_check_item_passes_json_body():
    spec = NetworkCheckSpec(
        name="missav_api",
        group="账号/API",
        url="https://client-rapi-missav.recombee.com/search",
        method="POST",
        json_data={"searchQuery": "ssni-647"},
    )
    client = FakeClient()

    await run_network_check_item(spec, client=client)

    assert client.calls[0]["json_data"] == {"searchQuery": "ssni-647"}


def test_format_result_line_does_not_duplicate_error():
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://example.test")
    from mdcx.core.network_check import NetworkCheckResult

    result = NetworkCheckResult(
        spec=spec,
        status=NetworkCheckStatus.FAILED,
        message="GET https://example.test 失败: HTTP 403",
        error="GET https://example.test 失败: HTTP 403",
    )

    line = format_result_line(result)

    assert line.count("GET https://example.test 失败: HTTP 403") == 1


@pytest.mark.anyio
async def test_run_network_check_item_uses_default_retry_instead_of_single_attempt():
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net")
    client = FakeClient()

    await run_network_check_item(spec, client=client)

    assert len(client.calls) == 1
    assert "retry_count" not in client.calls[0], "检测不应强制单次请求，偶发连接错误应走默认重试"


def test_is_cloudflare_challenge_does_not_misjudge_passive_script_injection():
    normal_page = (
        "<html>LibreFanza</html>"
        "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"
        "<script src='https://static.cloudflareinsights.com/beacon.min.js'></script>"
    )

    assert _is_cloudflare_challenge(normal_page) is False


def test_is_cloudflare_challenge_detects_orchestrate_challenge_page():
    challenge_page = (
        "<html><title>Just a moment...</title>"
        "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsd/v1/x.js'></script>"
        "<span>Checking your browser before accessing libredmm.com</span>"
        "</html>"
    )

    assert _is_cloudflare_challenge(challenge_page) is True


@pytest.mark.anyio
async def test_run_network_check_item_actively_uses_cf_bypass_on_challenge(monkeypatch: pytest.MonkeyPatch):
    class BypassConfig(FakeConfig):
        cf_bypass_url = "http://0.0.0.0:8000"

    class BypassManager:
        config = BypassConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: BypassManager())
    client = FakeBypassClient()
    spec = NetworkCheckSpec(
        name="cf-site",
        group="刮削站点",
        url="https://cf.example",
        enable_cf_bypass=True,
        headers={"cookie": "a=b"},
    )

    result = await run_network_check_item(spec, client=client)

    assert result.status == NetworkCheckStatus.OK
    assert result.message == "连接正常，已通过 CF Bypass（mirror）"
    assert client.bypass_calls[0]["target_url"] == "https://cf.example"
    assert client.bypass_calls[0]["headers"] == {"cookie": "a=b"}
    assert client.bypass_calls[0]["timeout"] is None


@pytest.mark.anyio
async def test_run_network_check_item_uses_trawl_adapter_when_only_external_cf_service_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    """议题 #77：只配「外部 CF 服务」(cf_bypass_trawl_url)、cf_bypass_url 留空时，
    检测链路必须一样走 bypass——适配层是运行时自动启动、地址挂在 client 实例上的，
    不能用 config.cf_bypass_url 把关。
    """

    class TrawlConfig(FakeConfig):
        cf_bypass_url = ""
        cf_bypass_trawl_url = "http://127.0.0.1:8191"

    class TrawlManager:
        config = TrawlConfig()
        computed = None

    class TrawlBypassClient(FakeBypassClient):
        def __init__(self, *, adapter_ok: bool = True):
            super().__init__()
            self.adapter_ok = adapter_ok
            self.adapter_started = 0

        async def _ensure_local_bypass(self):
            self.adapter_started += 1
            return self.adapter_ok

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: TrawlManager())
    client = TrawlBypassClient()
    spec = NetworkCheckSpec(
        name="cf-site",
        group="刮削站点",
        url="https://cf.example",
        enable_cf_bypass=True,
    )

    result = await run_network_check_item(spec, client=client)

    assert result.status == NetworkCheckStatus.OK
    assert "CF Bypass" in result.message
    assert client.bypass_calls, "bypass 一次都没被调用"


@pytest.mark.anyio
async def test_run_network_check_item_reports_cf_bypass_failure(monkeypatch: pytest.MonkeyPatch):
    class BypassConfig(FakeConfig):
        cf_bypass_url = "http://0.0.0.0:8000"

    class BypassManager:
        config = BypassConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: BypassManager())
    spec = NetworkCheckSpec(
        name="cf-site",
        group="刮削站点",
        url="https://cf.example",
        enable_cf_bypass=True,
    )

    result = await run_network_check_item(spec, client=FakeBypassClient(bypass_ok=False))

    assert result.status == NetworkCheckStatus.FAILED
    assert result.message == "Cloudflare Bypass 失败"
    assert result.error == "bypass failed"


class ProbeCrawler:
    def __init__(self, client, base_url="", browser=None):
        self.client = client
        self.base_url = base_url
        self.detail_urls: list[str] | None = ["https://example.test/works/1"]
        self.raise_not_implemented = False

    async def run(self, input_data):
        return SimpleNamespace(data=None, debug_info=SimpleNamespace(error=None))

    def new_context(self, input_data):
        return SimpleNamespace(input=input_data, debug=lambda msg: None)

    async def _generate_search_url(self, ctx):
        if self.raise_not_implemented:
            raise NotImplementedError
        return [f"{self.base_url}/works?q={ctx.input.number}"]

    def _get_headers(self, ctx):
        return None

    def _get_cookies(self, ctx):
        return None

    async def _parse_search_page(self, ctx, html, search_url):
        return self.detail_urls


class ProbeFakeClient:
    def __init__(self, text: str = "ok"):
        self.text = text
        self.calls: list[dict] = []

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = SimpleNamespace(status_code=200, text=self.text, url=url, headers={})
        response.encoding = "utf-8"
        return response, ""


def test_scrape_probe_attempt_timeout_ladder():
    """议题 #118：单站探测阶梯 30s → 45s → 60s，超出档数取最后一档。"""
    assert nc.SCRAPE_PROBE_TIMEOUT == 30.0
    assert nc.SCRAPE_PROBE_ATTEMPT_TIMEOUTS == (30.0, 45.0, 60.0)
    assert nc.scrape_probe_attempt_timeout(0) == 30.0
    assert nc.scrape_probe_attempt_timeout(1) == 45.0
    assert nc.scrape_probe_attempt_timeout(2) == 60.0
    assert nc.scrape_probe_attempt_timeout(3) == 60.0
    assert nc.scrape_probe_attempt_timeout(-1) == 30.0
    assert nc.scrape_probe_ladder_text() == "30s/45s/60s"


def test_transient_probe_result_classification():
    """议题 #118：只有瞬时性结果值得轮内再试，确定性结论首次即定论。"""
    assert nc._is_transient_probe_result("站点可达但刮削探测超时")
    assert nc._is_transient_probe_result("站点可达但搜索页请求失败: timeout")
    assert nc._is_transient_probe_result("站点可达但刮削探测异常: boom")
    assert nc._is_transient_probe_result("站点可达但刮削探测失败: 500")
    # 确定性：重试必然同样结论
    assert not nc._is_transient_probe_result("站点可达，但测试番号 SSNI-647 未被该站点收录")
    assert not nc._is_transient_probe_result("站点可达但搜索页被 Cloudflare 拦截")
    assert not nc._is_transient_probe_result("站点可达但无法自动探测刮削，可用设置页指定网址实测")
    assert not nc._is_transient_probe_result("连接正常，刮削正常")


@pytest.mark.anyio
async def test_probe_crawler_capability_passes_probe_timeout_to_search_request(monkeypatch: pytest.MonkeyPatch):
    """轮内递进重试的超时档必须落到搜索页请求上（否则第 2/3 次仍是 30s，重试无意义）。"""
    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: ProbeCrawler)
    client = ProbeFakeClient()
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    await _probe_crawler_capability(client, spec, probe_timeout=45.0)

    assert client.calls
    assert client.calls[0]["timeout"] == 45.0


@pytest.mark.anyio
async def test_probe_crawler_capability_defaults_to_first_round_timeout(monkeypatch: pytest.MonkeyPatch):
    """未指定 probe_timeout 时用首轮 30s。"""
    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: ProbeCrawler)
    client = ProbeFakeClient()
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    await _probe_crawler_capability(client, spec)

    assert client.calls[0]["timeout"] == nc.SCRAPE_PROBE_TIMEOUT == 30.0


@pytest.mark.anyio
async def test_probe_crawler_by_run_uses_passed_timeout(monkeypatch: pytest.MonkeyPatch):
    """重写 _run 的爬虫探测必须使用调用方传入的超时值。"""
    captured: dict = {}

    async def fake_wait_for(awaitable, timeout=None):
        captured["timeout"] = timeout
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(nc.asyncio, "wait_for", fake_wait_for)

    class RunOnlyCrawler(ProbeCrawler):
        async def _generate_search_url(self, ctx):
            return None

    status, message = await nc._probe_crawler_by_run(
        RunOnlyCrawler(ProbeFakeClient()), SimpleNamespace(number="X-1"), 60.0
    )

    assert captured["timeout"] == 60.0
    assert status == NetworkCheckStatus.WARNING
    assert "超时" in message


def _stub_probe_attempts(monkeypatch: pytest.MonkeyPatch, results: list[tuple[NetworkCheckStatus | None, str]]):
    """替换单轮探测，按顺序吐出 results（用完重复最后一条），记录每次超时值。"""
    seen: list[float | None] = []

    async def fake(client, spec, probe_timeout=None):
        seen.append(probe_timeout)
        index = min(len(seen) - 1, len(results) - 1)
        return results[index]

    monkeypatch.setattr(nc, "_probe_crawler_capability", fake)
    return seen


_PROBE_SPEC = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)


@pytest.mark.anyio
async def test_probe_retry_stops_when_a_later_attempt_passes(monkeypatch: pytest.MonkeyPatch):
    """议题 #118：首探超时后自动第 2 次并放宽到 45s，成功即定论，不再跑第 3 次。"""
    _stub_probe_attempts(
        monkeypatch,
        [
            (NetworkCheckStatus.WARNING, "站点可达但刮削探测超时"),
            (NetworkCheckStatus.OK, "连接正常，刮削正常"),
        ],
    )
    lines: list[str] = []

    status, message = await nc._probe_crawler_capability_with_retry(
        ProbeFakeClient(), _PROBE_SPEC, progress=lines.append
    )

    assert status == NetworkCheckStatus.OK
    assert "刮削正常" in message


@pytest.mark.anyio
async def test_probe_retry_three_timeouts_declares_probe_invalid(monkeypatch: pytest.MonkeyPatch):
    """三次都超时 → 给出「判定该站刮削探测无效」终局说明，并列出实际用过的阶梯。"""
    seen = _stub_probe_attempts(monkeypatch, [(NetworkCheckStatus.WARNING, "站点可达但刮削探测超时")])

    status, message = await nc._probe_crawler_capability_with_retry(ProbeFakeClient(), _PROBE_SPEC)

    assert seen == [30.0, 45.0, 60.0]
    assert status == NetworkCheckStatus.WARNING
    assert "3 次均超时" in message
    assert "30s/45s/60s" in message
    assert "判定该站刮削探测无效" in message


@pytest.mark.anyio
async def test_probe_retry_skips_deterministic_result(monkeypatch: pytest.MonkeyPatch):
    """「测试番号未被收录」是确定性结论：只探一次，不白等 45+60s。"""
    seen = _stub_probe_attempts(
        monkeypatch,
        [(NetworkCheckStatus.WARNING, "站点可达，但测试番号 SSNI-647 未被该站点收录（属正常情况）")],
    )

    status, message = await nc._probe_crawler_capability_with_retry(ProbeFakeClient(), _PROBE_SPEC)

    assert seen == [30.0]
    assert status == NetworkCheckStatus.WARNING
    assert "未被该站点收录" in message


@pytest.mark.anyio
async def test_probe_retry_emits_attempt_progress_lines(monkeypatch: pytest.MonkeyPatch):
    """重试要留痕，否则用户面对十几秒静默以为检测卡死。"""
    _stub_probe_attempts(monkeypatch, [(NetworkCheckStatus.WARNING, "站点可达但刮削探测超时")])
    lines: list[str] = []

    await nc._probe_crawler_capability_with_retry(ProbeFakeClient(), _PROBE_SPEC, progress=lines.append)

    assert any("avbase 第 2/3 次刮削探测" in line and "45s" in line for line in lines), lines
    assert any("avbase 第 3/3 次刮削探测" in line and "60s" in line for line in lines), lines
    assert not any("第 1/3 次" in line for line in lines), "首探无需额外提示"


@pytest.mark.anyio
async def test_probe_retry_stops_when_cancelled(monkeypatch: pytest.MonkeyPatch):
    """点「停止」后不再继续下一档探测。"""
    cancel_event = threading.Event()
    seen: list[float | None] = []

    async def fake(client, spec, probe_timeout=None):
        seen.append(probe_timeout)
        cancel_event.set()
        return NetworkCheckStatus.WARNING, "站点可达但刮削探测超时"

    monkeypatch.setattr(nc, "_probe_crawler_capability", fake)

    status, message = await nc._probe_crawler_capability_with_retry(
        ProbeFakeClient(), _PROBE_SPEC, cancel_event=cancel_event
    )

    assert seen == [30.0]
    assert status == NetworkCheckStatus.WARNING
    assert "超时" in message


@pytest.mark.anyio
async def test_probe_retry_mixed_transient_keeps_last_reason(monkeypatch: pytest.MonkeyPatch):
    """非全超时（请求失败/异常混合）时终局说明保留最后一次原因，避免误报「均超时」。"""
    _stub_probe_attempts(
        monkeypatch,
        [
            (NetworkCheckStatus.WARNING, "站点可达但搜索页请求失败: conn reset"),
            (NetworkCheckStatus.WARNING, "站点可达但刮削探测异常: boom"),
            (NetworkCheckStatus.WARNING, "站点可达但刮削探测失败: 500"),
        ],
    )

    status, message = await nc._probe_crawler_capability_with_retry(ProbeFakeClient(), _PROBE_SPEC)

    assert status == NetworkCheckStatus.WARNING
    assert "3 次均未通过" in message
    assert "最后一次" in message
    assert "500" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_ok_when_search_finds_detail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: ProbeCrawler)
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.OK
    assert "刮削正常" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_warns_on_cloudflare_challenge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: ProbeCrawler)
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    status, message = await _probe_crawler_capability(
        ProbeFakeClient(
            text="<html><script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsd/v1/x.js'></script>Cloudflare</html>"
        ),
        spec,
    )

    assert status == NetworkCheckStatus.WARNING
    assert "Cloudflare" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_warns_when_no_search_result(monkeypatch: pytest.MonkeyPatch):
    class NoResultCrawler(ProbeCrawler):
        def __init__(self, client, base_url="", browser=None):
            super().__init__(client, base_url, browser)
            self.detail_urls = None

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: NoResultCrawler)
    spec = NetworkCheckSpec(name="avbase", group="刮削站点", url="https://www.avbase.net", site=Website.AVBASE)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.WARNING
    assert "未被该站点收录" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_warns_when_search_url_unavailable(monkeypatch: pytest.MonkeyPatch):
    class NoUrlCrawler(ProbeCrawler):
        async def _generate_search_url(self, ctx):
            return None

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: NoUrlCrawler)
    spec = NetworkCheckSpec(name="fc2ppvdb", group="刮削站点", url="https://fc2cmadb.com", site=Website.FC2PPVDB)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.WARNING
    assert "未被该站点收录" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_falls_back_to_run_when_search_url_missing(monkeypatch: pytest.MonkeyPatch):
    """重写 _run 的爬虫（fc2/cnmdb 等）_generate_search_url 返回 None 时回退真实刮削探测."""

    class RunOnlyCrawler(ProbeCrawler):
        async def _generate_search_url(self, ctx):
            return None

        async def run(self, input_data):
            return SimpleNamespace(data=SimpleNamespace(), debug_info=SimpleNamespace(error=None))

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: RunOnlyCrawler)
    spec = NetworkCheckSpec(name="fc2ppvdb", group="刮削站点", url="https://fc2cmadb.com", site=Website.FC2PPVDB)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.OK
    assert "刮削正常" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_falls_back_to_run_when_parse_empty(monkeypatch: pytest.MonkeyPatch):
    """重写 _search（POST 搜索）的爬虫 GET 探测解析为空时回退真实刮削探测."""

    class PostSearchCrawler(ProbeCrawler):
        def __init__(self, client, base_url="", browser=None):
            super().__init__(client, base_url, browser)
            self.detail_urls = None

        async def run(self, input_data):
            return SimpleNamespace(data=SimpleNamespace(), debug_info=SimpleNamespace(error=None))

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: PostSearchCrawler)
    spec = NetworkCheckSpec(name="madouqu", group="刮削站点", url="https://madouqu.shop", site=Website.MADOUQU)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.OK
    assert "刮削正常" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_uses_probe_number_attribute(monkeypatch: pytest.MonkeyPatch):
    """爬虫类自定义 probe_number 时探测使用专属番号."""

    captured = {}

    class BrandedCrawler(ProbeCrawler):
        probe_number = "FNS-165"

        async def _generate_search_url(self, ctx):
            captured["number"] = ctx.input.number
            return [f"{self.base_url}/works?q={ctx.input.number}"]

        async def _parse_search_page(self, ctx, html, search_url):
            return ["https://example.test/works/1"]

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: BrandedCrawler)
    spec = NetworkCheckSpec(name="xcity", group="刮削站点", url="https://xcity.jp", site=Website.XCITY)

    status, _message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.OK
    assert captured["number"] == "FNS-165"


@pytest.mark.anyio
async def test_probe_crawler_capability_warns_when_run_rewritten_and_succeeds(monkeypatch: pytest.MonkeyPatch):
    """重写 _run 的 API 类爬虫走真实刮削探测, run() 成功时返回 OK."""

    class RewrittenCrawler(ProbeCrawler):
        def __init__(self, client, base_url="", browser=None):
            super().__init__(client, base_url, browser)
            self.raise_not_implemented = True

        async def run(self, input_data):
            return SimpleNamespace(data=SimpleNamespace(), debug_info=SimpleNamespace(error=None))

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: RewrittenCrawler)
    spec = NetworkCheckSpec(name="avmoo", group="刮削站点", url="https://avmoo.shop", site=Website.AVMOO)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.OK
    assert "刮削正常" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_warns_when_run_rewritten_and_fails(monkeypatch: pytest.MonkeyPatch):
    """重写 _run 的爬虫 run() 失败时返回 WARNING."""

    class RewrittenCrawler(ProbeCrawler):
        def __init__(self, client, base_url="", browser=None):
            super().__init__(client, base_url, browser)
            self.raise_not_implemented = True

        async def run(self, input_data):
            return SimpleNamespace(data=None, debug_info=SimpleNamespace(error="未找到匹配"))

    monkeypatch.setattr("mdcx.crawlers.get_crawler", lambda site: RewrittenCrawler)
    spec = NetworkCheckSpec(name="fc2ppvdb", group="刮削站点", url="https://fc2cmadb.com", site=Website.FC2PPVDB)

    status, message = await _probe_crawler_capability(ProbeFakeClient(), spec)

    assert status == NetworkCheckStatus.WARNING
    assert "刮削探测失败" in message


@pytest.mark.anyio
async def test_probe_crawler_capability_skipped_without_site():
    spec = NetworkCheckSpec(name="GitHub Raw", group="基础连通性", url="https://raw.githubusercontent.com")

    status, message = await _probe_crawler_capability(None, spec)

    assert status is None
    assert message == ""


def test_compute_used_proxy_false_when_proxy_disabled():
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://libredmm.com", use_proxy=True)

    assert _compute_used_proxy(spec) is False


def test_compute_used_proxy_true_when_host_in_proxy_sites(monkeypatch: pytest.MonkeyPatch):
    class ProxyConfig(FakeConfig):
        use_proxy = True
        proxy = "http://127.0.0.1:7890"
        proxy_sites = "libredmm.com,javdb.com"
        direct_sites = ""

    class ProxyManager:
        config = ProxyConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: ProxyManager())
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://libredmm.com", use_proxy=True)

    assert _compute_used_proxy(spec) is True


def test_compute_used_proxy_false_when_host_not_in_proxy_sites(monkeypatch: pytest.MonkeyPatch):
    class ProxyConfig(FakeConfig):
        use_proxy = True
        proxy = "http://127.0.0.1:7890"
        proxy_sites = "javdb.com"
        direct_sites = ""

    class ProxyManager:
        config = ProxyConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: ProxyManager())
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://libredmm.com", use_proxy=True)

    assert _compute_used_proxy(spec) is False


def test_compute_used_proxy_false_when_spec_forbids_proxy(monkeypatch: pytest.MonkeyPatch):
    class ProxyConfig(FakeConfig):
        use_proxy = True
        proxy = "http://127.0.0.1:7890"
        proxy_sites = "libredmm.com"
        direct_sites = ""

    class ProxyManager:
        config = ProxyConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: ProxyManager())
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://libredmm.com", use_proxy=False)

    assert _compute_used_proxy(spec) is False


def test_compute_used_proxy_false_when_host_in_direct_sites(monkeypatch: pytest.MonkeyPatch):
    """直连白名单优先：即使 host 在 proxy_sites 中，命中 direct_sites 也不走代理。"""

    class ProxyConfig(FakeConfig):
        use_proxy = True
        proxy = "http://127.0.0.1:7890"
        proxy_sites = "google.com,javdb.com"
        direct_sites = "google.com"

    class ProxyManager:
        config = ProxyConfig()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: ProxyManager())
    spec = NetworkCheckSpec(name="site", group="刮削站点", url="https://google.com", use_proxy=True)

    assert _compute_used_proxy(spec) is False


def test_format_result_line_shows_direct_when_not_using_proxy(monkeypatch: pytest.MonkeyPatch):
    from mdcx.core.network_check import NetworkCheckResult

    result = NetworkCheckResult(
        spec=NetworkCheckSpec(name="site", group="刮削站点", url="https://libredmm.com", use_proxy=True),
        status=NetworkCheckStatus.OK,
        message="连接正常，刮削正常",
        used_proxy=False,
    )

    line = format_result_line(result)

    assert "直连" in line


# ---- 站点检测缓存（持久化 & 合并，供站点选择列表回显）----


def _mk_cache_result(site, status, *, group="刮削站点", used_proxy=True):
    spec = NetworkCheckSpec(name=str(site), group=group, url="https://example.test", site=site)
    return NetworkCheckResult(spec=spec, status=status, message="", used_proxy=used_proxy)


def test_site_result_level_mapping():
    assert _site_result_level(NetworkCheckStatus.OK) == "ok"
    assert _site_result_level(NetworkCheckStatus.WARNING) == "warn"
    assert _site_result_level(NetworkCheckStatus.FAILED) == "fail"
    assert _site_result_level(NetworkCheckStatus.SKIPPED) == "skip"
    assert _site_result_level(NetworkCheckStatus.CANCELLED) == "skip"


def test_merge_and_load_cache_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(nc, "_site_cache_path", lambda: cache)

    merge_site_check_cache([_mk_cache_result(Website.JAVDB, NetworkCheckStatus.OK, used_proxy=True)])

    loaded = load_site_check_cache()
    assert loaded["javdb"]["status"] == "ok"
    assert loaded["javdb"]["route"] == "proxy"
    assert loaded["javdb"]["checked_at"]


def test_merge_cache_includes_any_site_attributed_group(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """议题 #129 语义反转: 缓存收集以"有无 site 归属"为准, 不再按组过滤——
    账号/API 组要标注; 无 site 归属项(基础连通性等)仍排除。"""
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(nc, "_site_cache_path", lambda: cache)

    merge_site_check_cache(
        [
            _mk_cache_result(Website.JAVDB, NetworkCheckStatus.OK, group="账号/API"),
            NetworkCheckResult(
                spec=NetworkCheckSpec(name="通用 HTTPS", group="基础环境", url="https://x.test"),
                status=NetworkCheckStatus.OK,
                message="m",
            ),
        ]
    )

    loaded = load_site_check_cache()
    assert loaded["javdb"]["status"] == "ok"
    assert len(loaded) == 1


def test_merge_cache_partial_overwrite_preserves_history(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(nc, "_site_cache_path", lambda: cache)

    merge_site_check_cache([_mk_cache_result(Website.JAVDB, NetworkCheckStatus.FAILED, used_proxy=False)])
    # 重试失败项场景：只重测部分站点，其余历史保留
    merge_site_check_cache([_mk_cache_result(Website.DMM, NetworkCheckStatus.OK)])

    loaded = load_site_check_cache()
    assert loaded["javdb"]["status"] == "fail"
    assert loaded["javdb"]["route"] == "direct"
    assert loaded["dmm"]["status"] == "ok"


def test_merge_cache_new_run_overwrites_same_site(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(nc, "_site_cache_path", lambda: cache)

    merge_site_check_cache([_mk_cache_result(Website.JAVDB, NetworkCheckStatus.FAILED)])
    merge_site_check_cache([_mk_cache_result(Website.JAVDB, NetworkCheckStatus.OK)])

    assert load_site_check_cache()["javdb"]["status"] == "ok"


def test_load_cache_bad_or_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(nc, "_site_cache_path", lambda: cache)
    assert load_site_check_cache() == {}
    cache.write_text("not-json", encoding="utf-8")
    assert load_site_check_cache() == {}
    cache.write_text('{"version":1,"sites":"oops"}', encoding="utf-8")
    assert load_site_check_cache() == {}


def test_region_tags_reference_valid_websites():
    # 地域标签数据源防漂移：键必须是真实存在的站点值
    from mdcx.manual import ManualConfig

    valid = {w.value for w in Website}
    assert set(ManualConfig.SITE_REGION_TAGS) <= valid
    assert set(ManualConfig.SITE_REGION_TAGS) == {"dmm", "mgstage", "javdb", "javdb_api"}


@pytest.mark.anyio
async def test_run_network_check_reports_structured_progress(monkeypatch: pytest.MonkeyPatch):
    async def fake_specs():
        return [
            NetworkCheckSpec(name="env", group="基础环境", url="https://env.example"),
            NetworkCheckSpec(name="a", group="基础连通性", url="https://a.example"),
            NetworkCheckSpec(name="b", group="刮削站点", url="https://b.example"),
            NetworkCheckSpec(name="c", group="刮削站点", url="https://c.example"),
        ]

    monkeypatch.setattr("mdcx.core.network_check.build_network_check_specs", fake_specs)
    seen: list[tuple[int, int]] = []

    results = await run_network_check(
        on_item_done=lambda done, total: seen.append((done, total)),
        client=FakeClient(),
        concurrency=3,
        emit_header=False,
    )

    # "基础环境"组不参与计数：total=3，done 单调递增到 3
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert len(results) == 3  # "基础环境"组是输出横幅非实际检测项，不执行也不计进度


# ==================== 议题 #129: API 组入缓存 + official 五站子检测/最差聚合 ====================


def _result(site: Website, status: NetworkCheckStatus, group: str = "刮削站点", name: str = "") -> NetworkCheckResult:
    spec = NetworkCheckSpec(name=name or site.value, group=group, url="https://x.test", site=site)
    return NetworkCheckResult(spec=spec, status=status, message="m", used_proxy=True)


def test_merge_cache_includes_api_group_sites(monkeypatch, tmp_path):
    """账号/API 组(带 site 归属)的检测结果须写入站点缓存→网站设置下拉有标注 (#129)。"""
    monkeypatch.setattr(nc, "_site_cache_path", lambda: tmp_path / "cache.json")
    merge_site_check_cache(
        [
            _result(Website.DMM_API, NetworkCheckStatus.OK, group="账号/API"),
            _result(Website.THEJAVDB_API, NetworkCheckStatus.WARNING, group="账号/API"),
            _result(Website.MISSAV_API, NetworkCheckStatus.FAILED, group="账号/API"),
            _result(None or Website.THEPORNDB, NetworkCheckStatus.OK, group="账号/API"),
            # 无站点归属项(基础环境等)不入库
            NetworkCheckResult(
                spec=NetworkCheckSpec(name="通用 HTTPS", group="基础连通性", url="https://x.test"),
                status=NetworkCheckStatus.OK,
                message="m",
            ),
        ]
    )
    cache = load_site_check_cache()
    assert cache["dmm_api"]["status"] == "ok"
    assert cache["thejavdb_api"]["status"] == "warn"
    assert cache["missav_api"]["status"] == "fail"
    assert cache["theporndb"]["status"] == "ok"
    assert "通用 HTTPS" not in cache and all(v.get("status") for v in cache.values())


def test_merge_cache_official_aggregates_worst(monkeypatch, tmp_path):
    """official 五站子结果按"取最差"聚合: 任一 fail 整条链路标红, 且不被后续 ok 覆盖。"""
    monkeypatch.setattr(nc, "_site_cache_path", lambda: tmp_path / "cache.json")
    merge_site_check_cache(
        [
            _result(Website.OFFICIAL, NetworkCheckStatus.OK, name="official·1pondo"),
            _result(Website.OFFICIAL, NetworkCheckStatus.FAILED, name="official·heyzo"),
            _result(Website.OFFICIAL, NetworkCheckStatus.OK, name="official·caribbeancom"),
        ]
    )
    assert load_site_check_cache()["official"]["status"] == "fail"

    # 反序: 先 fail 后 ok 也必须保持 fail
    monkeypatch.setattr(nc, "_site_cache_path", lambda: tmp_path / "cache2.json")
    merge_site_check_cache(
        [
            _result(Website.OFFICIAL, NetworkCheckStatus.FAILED, name="official·1pondo"),
            _result(Website.OFFICIAL, NetworkCheckStatus.OK, name="official·heyzo"),
        ]
    )
    assert load_site_check_cache()["official"]["status"] == "fail"


@pytest.mark.anyio
async def test_official_generates_five_sub_specs(monkeypatch):
    """official 未自定义 URL 时逐站生成 5 个子检测(报告每站一行)。"""
    from mdcx.crawlers.official_uncensored import UNCENSORED_OFFICIAL_SITES

    class OfficialCrawler:
        @classmethod
        def base_url_(cls):
            return ""

    class NoCustomConfig(FakeConfig):
        def get_site_url(self, site, default=""):
            return ""

    class Mgr:
        config = NoCustomConfig()
        computed = None

    fake_crawlers = SimpleNamespace(
        get_registered_crawler_sites=lambda include_hidden=False: [Website.OFFICIAL],
        get_crawler=lambda site: OfficialCrawler,
    )
    monkeypatch.setitem(sys.modules, "mdcx.crawlers", fake_crawlers)
    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: Mgr())

    specs = await build_network_check_specs()
    official_specs = [s for s in specs if s.site == Website.OFFICIAL]
    assert len(official_specs) == len(UNCENSORED_OFFICIAL_SITES) == 5
    assert {s.name for s in official_specs} == {f"official·{src}" for src in UNCENSORED_OFFICIAL_SITES}
    assert all(s.url and s.url.startswith("https://") for s in official_specs)


def test_theporndb_token_spec_binds_site_without_token():
    """未填 Token 时 ThePornDB 检测项也必须带 site，才能回标网站设置下拉（#174）。"""
    spec = next(s for s in nc._build_static_specs() if s.name == "ThePornDB Token")
    assert spec.site == Website.THEPORNDB
    assert spec.group == "账号/API"
    assert spec.warning_if_missing


def test_theporndb_token_spec_binds_site_with_token(monkeypatch):
    """已填 Token 时检测项同样带 site=theporndb（#174）。"""

    class Cfg(FakeConfig):
        theporndb_api_token = "tok"

    class Mgr:
        config = Cfg()
        computed = None

    monkeypatch.setattr("mdcx.core.network_check._manager", lambda: Mgr())
    spec = next(s for s in nc._build_static_specs() if s.name == "ThePornDB Token")
    assert spec.site == Website.THEPORNDB
    assert spec.validator == "theporndb_token"
    assert "api.theporndb.net" in spec.url


def test_merge_cache_uses_theporndb_token_production_spec(monkeypatch, tmp_path):
    """用生产形态的 ThePornDB Token spec（name 不是站点值）合并缓存，键必须是 theporndb。"""
    monkeypatch.setattr(nc, "_site_cache_path", lambda: tmp_path / "cache.json")
    spec = NetworkCheckSpec(
        name="ThePornDB Token",
        group="账号/API",
        url="https://api.theporndb.net/scenes/hash/x",
        site=Website.THEPORNDB,
        validator="theporndb_token",
    )
    merge_site_check_cache(
        [NetworkCheckResult(spec=spec, status=NetworkCheckStatus.OK, message="API Token 有效", used_proxy=True)]
    )
    cache = load_site_check_cache()
    assert cache["theporndb"]["status"] == "ok"


@pytest.mark.anyio
async def test_thejavdb_api_spec_has_site_for_badge(monkeypatch: pytest.MonkeyPatch):
    """thejavdb_api 生产检测项带 site 归属，网站设置下拉能回标（#129/#174）。"""

    class ThejavdbApiCrawlerStub:
        @classmethod
        def base_url_(cls):
            return "https://api.thejavdb.net/v1"

    fake_crawlers = SimpleNamespace(
        get_registered_crawler_sites=lambda include_hidden=False: [Website.THEJAVDB_API],
        get_crawler=lambda site: ThejavdbApiCrawlerStub,
    )
    monkeypatch.setitem(sys.modules, "mdcx.crawlers", fake_crawlers)

    specs = await build_network_check_specs()
    spec = next(s for s in specs if s.site == Website.THEJAVDB_API)
    assert spec.name == "thejavdb_api"
    assert spec.group == "账号/API"
    assert spec.validator == "thejavdb_api"
    assert spec.url.endswith("/movies?q=ssni-200")
