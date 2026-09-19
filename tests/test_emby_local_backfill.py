import pytest

from mdcx.models.flags import Flags
from mdcx.signals import signal
from mdcx.tools import emby_actor_info, emby_actor_manager


@pytest.fixture(autouse=True)
def reset_stop_flags():
    Flags.stop_requested = False
    signal.stop = False
    yield
    Flags.stop_requested = False
    signal.stop = False


def _actor(name="演员甲"):
    return {"Name": name, "ServerId": "s1", "Id": "1"}


def _mock_env(monkeypatch, local_data, post_ok=True):
    state = {"post": [], "wiki": 0, "minnano": 0, "db": 0}

    async def fake_get_actor_detail(actor):
        return {"Overview": ""}, ""

    def fake_generate_server_url(actor):
        return ("http://home", "", "", "", "", "http://update")

    async def fake_search_wiki(ai):
        state["wiki"] += 1
        return None, "wiki-none"

    async def fake_get_detail(res, msg, ai):
        return None, ""

    async def fake_get_minnano(ai, wiki_intro=""):
        state["minnano"] += 1
        return None, "minnano-none"

    def fake_db(ai):
        state["db"] += 1
        return 0, "db-none"

    class _Client:
        async def post_text(self, url, json_data=None, headers=None, use_proxy=False):
            state["post"].append(json_data)
            return (True, "") if post_ok else (None, "timeout")

    class _Computed:
        async_client = _Client()

    class _Ctx:
        async def __aenter__(self):
            return _Computed()

        async def __aexit__(self, *a):
            return False

    def fake_headers():
        return {}

    monkeypatch.setattr(emby_actor_info, "_get_actor_detail", fake_get_actor_detail)
    monkeypatch.setattr(emby_actor_info, "_generate_server_url", fake_generate_server_url)
    # 议题 #56: emby_actor_info 改为无条件 _build_jellyfin_headers(), 不再有 _is_jellyfin_server 分支
    monkeypatch.setattr(emby_actor_info, "_build_jellyfin_headers", fake_headers)
    monkeypatch.setattr(emby_actor_manager, "search_wiki", fake_search_wiki)
    monkeypatch.setattr(emby_actor_manager, "get_detail", fake_get_detail)
    monkeypatch.setattr(emby_actor_manager, "get_minnano_info", fake_get_minnano)
    monkeypatch.setattr(emby_actor_manager.ActressDB, "update_actor_info_from_db", staticmethod(fake_db))
    monkeypatch.setattr(emby_actor_manager.resources, "get_actor_data", lambda name: local_data)
    monkeypatch.setattr(emby_actor_manager.manager, "acquire_computed", lambda: _Ctx())
    return state


@pytest.mark.asyncio
async def test_local_hit_backfills_birth_date_and_bio(monkeypatch):
    state = _mock_env(
        monkeypatch,
        {"has_name": True, "birth_date": "1993-06-05", "bio": "身高158cm\n三围B86", "zh_cn": "阿部純子"},
    )
    flag, msg = await emby_actor_info._process_actor_async(_actor(), [])
    assert flag & 8
    payload = state["post"][0]
    assert payload["PremiereDate"] == "1993-06-05T00:00:00.0000000Z"
    assert payload["ProductionYear"] == 1993
    # 议题 #149/#171: dump() 出口清洗占位文案, 但 \n→<br/> 等合法结构原样保留(#171)
    assert "身高158cm<br/>三围B86" in payload["Overview"]
    assert state["wiki"] == 0 and state["minnano"] == 0 and state["db"] == 0
    assert "本地库命中" in msg


@pytest.mark.asyncio
async def test_local_hit_skips_external_sources(monkeypatch):
    state = _mock_env(monkeypatch, {"has_name": True, "birth_date": "1993-06-05", "bio": "简介", "zh_cn": "阿部純子"})
    await emby_actor_info._process_actor_async(_actor(), [])
    assert state["wiki"] == 0 and state["minnano"] == 0 and state["db"] == 0


@pytest.mark.asyncio
async def test_local_bio_empty_falls_back_for_overview(monkeypatch):
    state = _mock_env(monkeypatch, {"has_name": True, "birth_date": "1993-06-05", "bio": "", "zh_cn": "阿部純子"})
    from mdcx.config.manager import manager

    monkeypatch.setattr(manager.config, "use_database", True)
    flag, _ = await emby_actor_info._process_actor_async(_actor(), [])
    assert state["wiki"] == 1
    assert flag & 8
    assert state["post"][0]["PremiereDate"] == "1993-06-05T00:00:00.0000000Z"


@pytest.mark.asyncio
async def test_local_miss_runs_full_external_chain(monkeypatch):
    state = _mock_env(monkeypatch, {"has_name": False})
    from mdcx.config.manager import manager

    monkeypatch.setattr(manager.config, "use_database", True)
    flag, _ = await emby_actor_info._process_actor_async(_actor(), [])
    assert not (flag & 8)
    assert state["wiki"] == 1
    assert state["minnano"] == 1
    assert state["db"] == 1


@pytest.mark.asyncio
async def test_local_query_exception_not_blocking(monkeypatch):
    state = _mock_env(monkeypatch, None)

    def boom(name):
        raise RuntimeError("boom")

    monkeypatch.setattr(emby_actor_manager.resources, "get_actor_data", boom)
    from mdcx.config.manager import manager

    monkeypatch.setattr(manager.config, "use_database", True)
    flag, _ = await emby_actor_info._process_actor_async(_actor(), [])
    assert not (flag & 8)
    assert state["wiki"] == 1
    assert state["db"] == 1


@pytest.mark.asyncio
async def test_local_post_failure_returns_zero(monkeypatch):
    _mock_env(
        monkeypatch,
        {"has_name": True, "birth_date": "1993-06-05", "bio": "简介", "zh_cn": "阿部純子"},
        post_ok=False,
    )
    flag, msg = await emby_actor_info._process_actor_async(_actor(), [])
    assert flag == 0
    assert "更新失败" in msg


def test_extract_bio_tags_structured_fields():
    """简介含结构化字段时应抽剥为对应 Emby 标签。"""
    bio = "身高: 164cm | 罩杯: F | 三围: 88/60/93 | 生涯: 2020~ | 出身: 宮城県 | 血型: A型 | 事务所: JETSTREAM(元・VERGER)"
    tags = emby_actor_manager._extract_bio_tags(bio)
    assert "身高: 164cm" in tags
    assert "罩杯: F" in tags
    assert "三围: 88/60/93" in tags
    assert "生涯: 2020~" in tags
    assert "出身: 宮城県" in tags
    assert "血型: A型" in tags
    assert not any(t.startswith("事务所") for t in tags)


def test_extract_bio_tags_empty_and_plain_bio():
    """无结构化字段或空文本不应抽出任何标签。"""
    assert emby_actor_manager._extract_bio_tags("") == []
    assert emby_actor_manager._extract_bio_tags("身高158cm\n三围B86") == []


@pytest.mark.asyncio
async def test_local_hit_extracts_tags_from_structured_bio(monkeypatch):
    """本地命中且简介为 minnano 风格一行时，生日/简介回填的同时应抽剥出标签。"""
    state = _mock_env(
        monkeypatch,
        {
            "has_name": True,
            "birth_date": "1993-11-23",
            "bio": "身高: 164cm | 罩杯: F | 三围: 88/60/93 | 生涯: 2020~ | 出身: 宮城県",
            "zh_cn": "七瀬いおり",
        },
    )
    flag, _ = await emby_actor_info._process_actor_async(_actor("七瀬いおり"), [])
    assert flag & 8
    payload = state["post"][0]
    tags = payload.get("Tags", [])
    assert "身高: 164cm" in tags
    assert "三围: 88/60/93" in tags
    assert "生涯: 2020~" in tags
    assert state["wiki"] == 0 and state["minnano"] == 0 and state["db"] == 0
