from __future__ import annotations

import asyncio
import json
import os
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import aiofiles
import aiofiles.os
from parsel import Selector

from ..base.web import download_file_with_filepath
from ..config.manager import manager
from ..config.resources import resources
from ..models.emby import (
    EMbyActressInfo,
    clean_overview_text,
    normalize_premiere_date,
    normalize_production_year,
)
from ..models.flags import Flags
from ..signals import signal
from ..utils import executor
from ..utils.file import write_file_atomic_async
from .actress_db import ActressDB
from .emby_shared import (  # noqa: F401
    _append_query,
    _build_jellyfin_headers,
    _emby_api_prefix,
    _emby_get_json,
    _emby_request,
    _generate_server_url,
    _is_jellyfin_server,
    _upload_actor_photo,
)
from .minnano_crawler import get_minnano_info
from .wiki import get_detail, search_wiki

_BIO_TAG_PATTERNS = (
    (r"身高:\s*([0-9.]+)\s*cm", "身高: {0}cm"),
    (r"罩杯:\s*([^\s/|]+)", "罩杯: {0}"),
    (r"三围:\s*([0-9]+/[0-9]+/[0-9]+)", "三围: {0}"),
    (r"生涯:\s*([0-9~\-]+)", "生涯: {0}"),
    (r"出身:\s*([^\s|]+)", "出身: {0}"),
    (r"血型:\s*([A-O]+型)", "血型: {0}"),
)

# 同步并发上限：Emby/Jellyfin 无速率压力，但图片上传 body 较大，4 并发平衡带宽
SYNC_CONCURRENCY = 4


def _extract_bio_tags(bio: str) -> list[str]:
    """从 actor_db 简介文本中抽剥结构化字段为 Emby 标签。

    格式与 actor_db_tool._build_bio_line 保持一致（`键: 值 | ...`）。
    """
    return [fmt.format(m.group(1)) for pat, fmt in _BIO_TAG_PATTERNS if (m := re.search(pat, bio))]


class ActorTaskStopped(Exception):
    pass


def _is_stop_requested() -> bool:
    return signal.stop or Flags.stop_requested


def _raise_if_stop_requested() -> None:
    if _is_stop_requested():
        raise ActorTaskStopped("手动停止")


@dataclass
class ActorInfo:
    name: str
    actor_id: str
    server_id: str
    has_image: bool = False
    has_overview: bool = False
    existing_overview: str = ""
    existing_taglines: list[str] = field(default_factory=list)
    existing_production_year: int | None = None
    existing_premiere_date: str = ""
    existing_production_locations: list[str] = field(default_factory=list)
    existing_provider_ids: dict[str, str] = field(default_factory=dict)
    existing_genres: list[str] = field(default_factory=list)
    existing_tags: list[str] = field(default_factory=list)
    new_overview: str = ""
    new_taglines: list[str] = field(default_factory=list)
    new_production_year: int | None = None
    new_premiere_date: str = ""
    new_production_locations: list[str] = field(default_factory=list)
    new_provider_ids: dict[str, str] = field(default_factory=dict)
    new_image_path: str | None = None
    new_backdrop_path: str | None = None
    movie_count: int = 0
    movie_titles: list[str] = field(default_factory=list)
    has_backdrop: bool = False
    need_update_info: bool = False
    need_update_image: bool = False
    need_update_backdrop: bool = False

    @property
    def status_text(self) -> str:
        parts = []
        parts.append("有头像" if self.has_image else "缺头像")
        parts.append("有简介" if self.has_overview else "缺简介")
        parts.append(f"{self.movie_count}部关联影片")
        return " | ".join(parts)

    @property
    def status_icon(self) -> str:
        if self.has_image and self.has_overview:
            return "✅"
        if not self.has_image and not self.has_overview:
            return "❌"
        return "⚠️"


async def get_emby_actor_list(filter_actor_only: bool = True) -> list[dict]:
    _raise_if_stop_requested()
    base_url = str(manager.config.emby_url).rstrip("/")
    headers = _build_jellyfin_headers()
    if "emby" == manager.config.server_type:
        server_name = "Emby"
        params: dict[str, str | None] = {
            "userId": manager.config.user_id,
            "fields": "Overview,ProviderIds,ProductionLocations,Taglines,Genres,Tags,PremiereDate,ProductionYear",
            "enableImages": "true",
        }
        if filter_actor_only:
            params["personTypes"] = "Actor"
        url = _append_query(base_url + "/emby/Persons", params)
    else:
        server_name = "Jellyfin"
        params = {
            "fields": "Overview,ProviderIds,ProductionLocations,Taglines,Genres,Tags",
            "enableImages": "true",
            "userId": manager.config.user_id,
        }
        if filter_actor_only:
            params["personTypes"] = "Actor"
        url = _append_query(base_url + "/Persons", params)
    signal.show_log_text(f"⏳ 连接 {server_name} 服务器...")
    if not manager.config.api_key:
        signal.show_log_text(f"🔴 {server_name} API 密钥未填写！")
        return []
    async with manager.acquire_computed() as computed:
        response, error = await computed.async_client.get_json(url, headers=headers, use_proxy=False)
    _raise_if_stop_requested()
    if response is None:
        signal.show_log_text(f"🔴 {server_name} 连接失败！{error}")
        return []
    actor_list = response.get("Items", [])
    signal.show_log_text(f"✅ {server_name} 连接成功！共 {len(actor_list)} 个演员")
    return actor_list


async def get_media_folders() -> list[dict]:
    # 议题 #133: 用轻量直连 httpx(无指纹/无池/无限流), 避免对内网 Emby 握手拖分钟
    response, error = await _emby_get_json(f"{_emby_api_prefix()}/Library/MediaFolders")
    if response is None:
        signal.show_log_text(f"🔴 获取媒体库列表失败！{error}")
        return []
    return response.get("Items", [])


# 演员详情进程内缓存（TTL 5 分钟）：数据准备阶段同一演员可能被多次请求详情，避免重复网络请求
_ACTOR_DETAIL_CACHE: dict[str, tuple[float, dict]] = {}
_ACTOR_DETAIL_CACHE_TTL = 300.0


async def fetch_actor_detail(actor_name: str) -> dict | None:
    now = time.monotonic()
    cached = _ACTOR_DETAIL_CACHE.get(actor_name)
    if cached is not None and now - cached[0] < _ACTOR_DETAIL_CACHE_TTL:
        return cached[1]

    base_url = str(manager.config.emby_url).rstrip("/")
    headers = _build_jellyfin_headers()
    from urllib.parse import quote

    name_encoded = quote(actor_name, safe="")
    if "emby" == manager.config.server_type:
        url = f"{base_url}/emby/Persons/{name_encoded}"
    else:
        url = _append_query(
            f"{base_url}/Persons/{name_encoded}",
            {"userId": manager.config.user_id},
        )
    async with manager.acquire_computed() as computed:
        response, error = await computed.async_client.get_json(url, headers=headers, use_proxy=False)
    if response is not None:
        # 缓存数量有界，避免长时间运行内存增长
        if len(_ACTOR_DETAIL_CACHE) >= 2000:
            _ACTOR_DETAIL_CACHE.clear()
        _ACTOR_DETAIL_CACHE[actor_name] = (time.monotonic(), response)
    return response


async def fetch_person_item_stats(
    parent_ids: list[str] | None = None,
    filter_actor_only: bool = True,
    page_limit: int = 500,
) -> tuple[dict[str, int], dict[str, list[str]], set]:
    """按分页统计各影片的出演演员。

    议题 #32：>1W 演员的服务器上 Limit=100000 单次响应过大导致服务端组装超时
    （真机 3 连超时后放弃整库，只剩另一个库的 66 个）。改为 StartIndex 分页拉取，
    并用 IncludeItemTypes/EnableImages/EnableUserData 缩减响应体积。
    """
    counts: dict = {}
    titles: dict = {}
    person_names: set = set()
    headers = _build_jellyfin_headers()
    if parent_ids:
        # 每个媒体库独立分页；不带 ParentId 时统一走单循环（lib_id=None 不拼参数）
        target_lib_ids: list[str | None] = list(parent_ids)
    else:
        target_lib_ids = [None]

    def _items_path(lib_id: str | None, start_index: int) -> str:
        prefix = _emby_api_prefix()
        path = (
            f"{prefix}/Items?"
            "Recursive=true&Fields=People"
            "&IncludeItemTypes=Movie,Episode"
            "&EnableImages=false&EnableUserData=false"
            f"&StartIndex={start_index}&Limit={page_limit}"
        )
        if lib_id:
            path += f"&ParentId={lib_id}"
        return path

    # 议题 #133: 出演统计是批量/分页路径(几十页), 走轻量直连避免多次指纹握手拖慢
    for lib_id in target_lib_ids:
        start_index = 0
        while True:
            response, _ = await _emby_request("GET", _items_path(lib_id, start_index), headers=headers)
            if response is None:
                # 该库失败(如超时)只跳过本库, 其余库照常统计
                break
            try:
                data = response.json()
            except Exception:
                break
            items = data.get("Items", [])
            if not items:
                break
            for item in items:
                people = item.get("People") or []
                item_name = item.get("Name", "")
                item_type = item.get("Type", "")
                seen_in_item = set()
                for person in people:
                    # filter_actor_only: 只统计 Type=Actor 的角色（Emby 默认返回导演/编剧等）
                    if filter_actor_only and person.get("Type") not in ("Actor", None):
                        continue
                    name = person.get("Name", "")
                    if not name:
                        continue
                    seen_in_item.add(name)
                    person_names.add(name)
                for name in seen_in_item:
                    counts[name] = counts.get(name, 0) + 1
                    if name not in titles:
                        titles[name] = []
                    titles[name].append(f"[{item_type}] {item_name}")
            # TotalRecordCount 缺失/为 0 时不能短路退出：start_index(500) >= 0
            # 恒真会让循环只拉第一页，出演统计大面积缺失——此时退化为
            # 仅靠短页信号（len(items) < page_limit）判定终止（全库审查 M7）
            total_count = data.get("TotalRecordCount")
            start_index += page_limit
            if (total_count is not None and int(total_count) > 0 and start_index >= int(total_count)) or (
                len(items) < page_limit
            ):
                break
    return counts, titles, person_names


async def fetch_all_actors(
    filter_actor_only: bool = True,
    deduplicate: bool = True,
    parent_ids: list[str] | None = None,
    progress_callback: Callable | None = None,
    concurrency: int = 8,
) -> tuple[list[ActorInfo], int]:
    persons = await get_emby_actor_list(filter_actor_only=filter_actor_only)
    if not persons:
        return [], 0
    seen_names = set()
    person_counts, person_titles, lib_person_names = await fetch_person_item_stats(
        parent_ids=parent_ids, filter_actor_only=filter_actor_only
    )

    # 第一遍: 过滤+构建 stub (不发起网络请求)
    stubs: list[tuple[int, ActorInfo, dict]] = []  # (原索引, actor_stub, person_raw)
    skipped_not_in_lib: list[str] = []  # 指定媒体库过滤但不在影片 People 里
    # 议题 #157: raw_count 语义 =「过滤后(库+角色)、去重前」条目数——统计栏「原始条目数」
    # 与「重复 = raw − 唯一名字」都以过滤后的演员集合为基准, 不能含被剔除的非演出角色。
    filtered_raw = 0
    for i, p in enumerate(persons):
        _raise_if_stop_requested()
        name = p.get("Name", "")
        if not name:
            continue
        # 议题 #157: Emby 的 /Persons 端点不支持按角色过滤——personTypes 仅在配合 Person
        # 参数时生效（官方 API 参考），服务端把导演/编剧/制片等非演出角色一并返回，
        # 此前"只看演员"开关对 Emby 从未真正生效（Jellyfin 端 personTypes 有效）。
        # 统一改为与「所选库(缺省全库)影片 People 中角色=Actor 的人名集合」交集过滤；
        # 出演统计整体失败导致集合为空时不过滤（兜底防误删，与 #32 教训一致）。
        if (parent_ids or filter_actor_only) and lib_person_names and name not in lib_person_names:
            skipped_not_in_lib.append(name)
            continue
        filtered_raw += 1
        if deduplicate:
            if name in seen_names:
                continue
            seen_names.add(name)
        actor_id = p.get("Id", "")
        server_id = p.get("ServerId", "")
        image_tags = p.get("ImageTags") or {}
        backdrop_tags = p.get("BackdropImageTags") or []
        info = ActorInfo(
            name=name,
            actor_id=actor_id,
            server_id=server_id,
            has_image="Primary" in image_tags,
            has_backdrop=len(backdrop_tags) > 0,
        )
        info.movie_count = person_counts.get(name, 0)
        info.movie_titles = person_titles.get(name, [])
        stubs.append((i, info, p))

    # 透明化跳过原因——小白至少看得见"为什么 XX 没在列表里"
    if skipped_not_in_lib:
        preview = ", ".join(skipped_not_in_lib[:5])
        more = f" 等共 {len(skipped_not_in_lib)} 人" if len(skipped_not_in_lib) > 5 else ""
        signal.show_log_text(
            f"⚠️ 跳过 {len(skipped_not_in_lib)} 个不在所选媒体库影片中出演(Actor)的人员: {preview}{more}"
        )

    # 第二遍: 并发抓详情 (注意限流——Emby/Jellyfin 一般无速率压力, 8 并发保守)
    # 列表请求已带 fields 时可直接复用 Item 中的详情字段, 避免逐人二次请求
    total = len(stubs)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    done_count = 0
    done_lock = asyncio.Lock()

    async def _fill(info: ActorInfo, person: dict) -> None:
        nonlocal done_count
        async with semaphore:
            _raise_if_stop_requested()
            if any(k in person for k in ("Overview", "Taglines", "ProductionYear")):
                detail: dict | None = person
            else:
                detail = await fetch_actor_detail(info.name)
        if detail:
            overview = detail.get("Overview") or ""
            info.has_overview = bool(overview)
            info.existing_overview = overview
            info.existing_taglines = detail.get("Taglines") or []
            info.existing_production_year = detail.get("ProductionYear")
            info.existing_premiere_date = detail.get("PremiereDate") or ""
            info.existing_production_locations = detail.get("ProductionLocations") or []
            info.existing_provider_ids = detail.get("ProviderIds") or {}
            info.existing_genres = detail.get("Genres") or []
            info.existing_tags = detail.get("Tags") or []
        async with done_lock:
            done_count += 1
            if progress_callback:
                progress_callback(done_count, total, info.name)

    await asyncio.gather(*(_fill(info, p) for _, info, p in stubs))

    # 按原顺序返回 (稳定性)
    return [info for _, info, _ in stubs], filtered_raw


def _gfriends_cdn_url(gfriends_github) -> str:
    """把 Gfriends GitHub 仓库地址转为 jsdelivr CDN 地址（@master 分支形式）。

    jsdelivr gh 路由格式为 ``gh/{user}/{repo}@{branch}/path``，把 ``/master/`` 直接
    拼在路径里会被当作仓库内文件路径导致 404。
    """
    repo_path = str(gfriends_github).strip(" /").rstrip("/")
    if "github.com/" in repo_path:
        repo_path = repo_path.split("github.com/", 1)[1]
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return f"https://cdn.jsdelivr.net/gh/{repo_path}@master"


async def get_gfriends_index() -> dict[str, str] | None:
    """加载 Gfriends 头像索引，返回 {filename: url} 字典，失败返回 None。

    优先使用本地仓库；否则从网络下载并缓存到 gfriends.json。
    包含版本检测：查询远程 commits 页面，仅在过期时重新下载。
    """
    gfriends_github = manager.config.gfriends_github
    gfriends_local_path = manager.config.gfriends_local_path
    # 优先使用 jsdelivr CDN（国内可达性优于 raw.githubusercontent.com）
    # jsdelivr gh 路由格式为 gh/{user}/{repo}@{branch}/path，/master/ 会被当作仓库内路径导致 404
    raw_url = _gfriends_cdn_url(gfriends_github)
    gfriends_json_path = resources.u("gfriends.json")

    def _expand(data: dict) -> dict[str, str]:
        """将 Filetree.json 原始格式展开为 {filename: url}；已展开则原样返回。"""
        content = data.get("Content") if isinstance(data, dict) else None
        if not content:
            return data if isinstance(data, dict) else {}
        result: dict[str, str] = {}
        for category, items in content.items():
            for filename, filepath in items.items():
                if filename not in result:
                    result[filename] = f"{raw_url}/Content/{category}/{filepath}"
        return result

    # 1) 本地仓库优先
    if gfriends_local_path and os.path.isdir(gfriends_local_path):
        local_filetree = os.path.join(gfriends_local_path, "Filetree.json")
        if os.path.isfile(local_filetree):
            try:
                async with aiofiles.open(local_filetree, encoding="utf-8") as f:
                    data = json.loads(await f.read())
                return _expand(data)
            except Exception as e:
                signal.show_log_text(f"🔴 本地仓库解析失败: {e}，回退到网络")

    # 2) 版本检测
    update_data = False
    net_float = 0.0
    if not await aiofiles.os.path.exists(gfriends_json_path):
        update_data = True
    elif await aiofiles.os.path.getmtime(gfriends_json_path) < 1657285200:
        update_data = True
    else:
        signal.show_log_text("⏳ 连接 Gfriends 网络头像库...")
        net_url = f"{gfriends_github}/commits/master/Filetree.json"
        async with manager.acquire_computed() as computed:
            response, _ = await computed.async_client.get_text(net_url)
        if response is None:
            signal.show_log_text("🔴 Gfriends 查询最新数据更新时间失败！")
            update_data = True
        else:
            net_time = ""
            try:
                from datetime import UTC, datetime

                date_time = re.findall(r'committedDate":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', response)
                latest_time = datetime.strptime(date_time[0], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
                net_float = latest_time.timestamp()
                net_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(net_float))
                signal.show_log_text(f"✅ Gfriends 连接成功！最新数据更新时间: {net_time}")
            except Exception:
                signal.show_log_text("🔶 Gfriends 历史页面解析失败，将强制重新下载数据表")
                update_data = True

            if not update_data:
                local_float = await aiofiles.os.path.getmtime(gfriends_json_path)
                local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(local_float))
                if not net_float or net_float > local_float:
                    signal.show_log_text(f"🍉 本地缓存数据需要更新！本地数据更新时间: {local_time}")
                    update_data = True
                else:
                    signal.show_log_text(f"✅ 本地缓存数据无需更新！本地数据更新时间: {local_time}")
                    try:
                        async with aiofiles.open(gfriends_json_path, encoding="utf-8") as f:
                            data = json.loads(await f.read())
                        return _expand(data)
                    except Exception:
                        signal.show_log_text("🔴 本地缓存数据读取失败！需重新缓存！")
                        update_data = True

    # 3) 下载并缓存
    if update_data:
        signal.show_log_text("⏳ 开始缓存 Gfriends 最新数据表...")
        filetree_url = f"{raw_url}/Filetree.json"
        async with manager.acquire_computed() as computed:
            filetree_response, _ = await computed.async_client.get_content(filetree_url)
        if filetree_response is None:
            signal.show_log_text("🔴 Gfriends 数据表获取失败！尝试使用本地缓存...")
            if await aiofiles.os.path.exists(gfriends_json_path):
                try:
                    async with aiofiles.open(gfriends_json_path, encoding="utf-8") as f:
                        data = json.loads(await f.read())
                    stale = _expand(data)
                    if stale:
                        signal.show_log_text("🔶 已使用本地缓存（可能已过期）")
                        return stale
                except Exception:
                    signal.show_log_text("🔴 本地缓存读取失败！")
            return None
        try:
            data = json.loads(filetree_response.decode("utf-8"))
            expanded = _expand(data)
            await write_file_atomic_async(
                gfriends_json_path,
                json.dumps(expanded, ensure_ascii=False, sort_keys=True, indent=4, separators=(",", ": ")),
            )
            signal.show_log_text("✅ Gfriends 数据表已缓存！")
            return expanded
        except Exception:
            signal.show_log_text("🔴 Gfriends 数据表展开失败！")
            return _expand(data) if isinstance(data, dict) else None

    return None


async def update_person_info(actor: ActorInfo) -> tuple[bool, str]:
    _, _, _, _, _, update_url = _generate_server_url(
        {"Name": actor.name, "Id": actor.actor_id, "ServerId": actor.server_id}
    )
    # 议题 #149: 简介出口统一清洗(段落标题/<br>/占位文案), 存量脏数据随下次同步自愈
    overview = clean_overview_text((actor.new_overview or actor.existing_overview or "").replace("\n", "<br/>"))
    # Genres/Tags/ProviderIds 必须恒为集合：Emby/Jellyfin 的 UpdateItem 会直接
    # Distinct()/ToList() 反序列化后的字段，缺省为 null 时抛 ArgumentNullException
    # （"Value cannot be null. (Parameter 'source')"）导致 HTTP 400（议题 #148）。
    # 无新值时回填服务器已有值，既不覆盖也避免空引用。
    provider_ids = dict(actor.existing_provider_ids or {})
    provider_ids.update({k: v for k, v in (actor.new_provider_ids or {}).items() if v})
    payload: dict[str, object] = {
        "Name": actor.name,
        "Id": actor.actor_id,
        "ServerId": actor.server_id,
        "Genres": [g for g in (actor.existing_genres or []) if g],
        "Tags": [t for t in (actor.existing_tags or []) if t],
        "ProviderIds": {k: v for k, v in provider_ids.items() if v},
    }
    if overview:
        payload["Overview"] = overview
    if actor.new_taglines:
        payload["Taglines"] = actor.new_taglines
    if actor.new_production_locations:
        payload["ProductionLocations"] = actor.new_production_locations
    year = normalize_production_year(actor.new_production_year)
    if year is not None:
        payload["ProductionYear"] = year
    premiere_date = normalize_premiere_date(actor.new_premiere_date)
    if premiere_date:
        payload["PremiereDate"] = premiere_date
    # 议题 #56: Emby 4.9 对 POST /Items/{id} 无 Content-Type 的 JSON body 判 400
    headers = _build_jellyfin_headers({"Content-Type": "application/json"})
    # 议题 #133: 信息更新是同步批量路径(每演员一次), 走轻量直连
    body, err = await _emby_request("POST", update_url, headers=headers, data=json.dumps(payload))
    # Emby POST 成功常返回 200/204 + 空 body; 不能 iff "ok": 空 bytes 是 falsy
    if err == "" and body is not None:
        return True, f"✅ {actor.name} 信息更新成功"
    return False, f"❌ {actor.name} 信息更新失败: {err or '服务器返回空响应'}"


async def clean_actor_data(actor: ActorInfo, new_overview: str, fix_birth: bool) -> tuple[bool, str]:
    """议题 #149: 原地清洗服务器存量演员数据(简介噪声/非法生日), 不经取数流程。

    与 update_person_info 的区别: 允许显式写入空简介(清除占位文案)、把非法生日
    (0000-00-00 等)重置为 Emby 原生零值 0001-01-01(服务器与列表均按未设置展示)。
    Genres/Tags/ProviderIds 恒回填服务器已有值, 避免 Emby 4.9 空引用 400(议题 #148)。
    """
    _, _, _, _, _, update_url = _generate_server_url(
        {"Name": actor.name, "Id": actor.actor_id, "ServerId": actor.server_id}
    )
    payload: dict[str, object] = {
        "Name": actor.name,
        "Id": actor.actor_id,
        "ServerId": actor.server_id,
        "Genres": [g for g in (actor.existing_genres or []) if g],
        "Tags": [t for t in (actor.existing_tags or []) if t],
        "ProviderIds": {k: v for k, v in (actor.existing_provider_ids or {}).items() if v},
    }
    if new_overview != (actor.existing_overview or ""):
        payload["Overview"] = new_overview
    if fix_birth:
        payload["PremiereDate"] = "0001-01-01T00:00:00.0000000Z"
    # 议题 #56: Emby 4.9 对 POST /Items/{id} 无 Content-Type 的 JSON body 判 400
    headers = _build_jellyfin_headers({"Content-Type": "application/json"})
    body, err = await _emby_request("POST", update_url, headers=headers, data=json.dumps(payload))
    if err == "" and body is not None:
        return True, f"✅ {actor.name} 数据清洗成功"
    return False, f"❌ {actor.name} 数据清洗失败: {err or '服务器返回空响应'}"


async def clean_actor_data_batch_async(
    items: list[tuple[ActorInfo, str, bool]],
    progress_callback: Callable | None = None,
    actor_callback: Callable | None = None,
) -> tuple[int, int]:
    """议题 #149: 批量清洗存量数据, 与 sync_batch 同一并发/进度模型。"""
    if not items:
        return 0, 0
    total = len(items)
    sem = asyncio.Semaphore(SYNC_CONCURRENCY)
    completed = 0

    async def _one(item: tuple[ActorInfo, str, bool]) -> bool:
        nonlocal completed
        actor, new_overview, fix_birth = item
        async with sem:
            ok, _msg = await clean_actor_data(actor, new_overview, fix_birth)
        completed += 1
        if progress_callback:
            progress_callback(completed, total, f"正在清洗: {actor.name} ({completed}/{total})")
        if actor_callback:
            actor_callback(actor, ok, _msg)
        return ok

    results = await asyncio.gather(*(_one(i) for i in items))
    success = sum(1 for r in results if r)
    return success, len(results) - success


def clean_actor_data_batch(
    items: list[tuple[ActorInfo, str, bool]],
    progress_callback: Callable | None = None,
    actor_callback: Callable | None = None,
) -> tuple[int, int]:
    return executor.run(clean_actor_data_batch_async(items, progress_callback, actor_callback))


async def upload_actor_image(actor: ActorInfo, image_path: str | Path) -> tuple[bool, str]:
    _, _, pic_url, _, _, _ = _generate_server_url(
        {"Name": actor.name, "Id": actor.actor_id, "ServerId": actor.server_id}
    )
    img_path = Path(image_path)
    if not img_path.exists():
        return False, f"❌ 图片文件不存在: {image_path}"

    ok, err = await _upload_actor_photo(pic_url, img_path)
    if ok:
        return True, f"✅ {actor.name} 头像上传成功"
    return False, f"❌ {actor.name} 头像上传失败: {err or '服务器返回空响应'}"


async def delete_actor_image(actor: ActorInfo) -> tuple[bool, str]:
    base_url = str(manager.config.emby_url).rstrip("/")
    if "emby" == manager.config.server_type:
        url = f"{base_url}/emby/Items/{actor.actor_id}/Images/Primary"
    else:
        url = f"{base_url}/Items/{actor.actor_id}/Images/Primary"
    headers = _build_jellyfin_headers()
    # 议题 #133: 删除属同步批量路径, 走轻量直连
    resp, err = await _emby_request("DELETE", url, headers=headers)
    if resp is None:
        # _emby_request 对 HTTP>=400 返回 (None, "HTTP 404...")，404 表示本来就没有，也视为"删干净了"
        if "404" in str(err):
            return True, f"✅ {actor.name} 旧头像本来就不存在 (HTTP 404)"
        return False, f"❌ {actor.name} 删除旧头像请求失败: {err}"
    status = int(resp.status_code)
    if status in (200, 204, 404):
        # 200/204 删除成功; 404 表示本来就没有, 也视为"删干净了"以便后续上传
        return True, f"✅ {actor.name} 旧头像已删除 (HTTP {status})"
    return False, f"❌ {actor.name} 删除旧头像失败: HTTP {status}"


async def delete_actor_backdrop(actor: ActorInfo) -> tuple[bool, str]:
    base_url = str(manager.config.emby_url).rstrip("/")
    if "emby" == manager.config.server_type:
        url = f"{base_url}/emby/Items/{actor.actor_id}/Images/Backdrop/0"
    else:
        url = f"{base_url}/Items/{actor.actor_id}/Images/Backdrop/0"
    headers = _build_jellyfin_headers()
    resp, err = await _emby_request("DELETE", url, headers=headers)
    if resp is None:
        if "404" in str(err):
            return True, f"✅ {actor.name} 旧背景本来就不存在 (HTTP 404)"
        return False, f"❌ {actor.name} 删除旧背景请求失败: {err}"
    status = int(resp.status_code)
    if status in (200, 204, 404):
        return True, f"✅ {actor.name} 旧背景已删除 (HTTP {status})"
    return False, f"❌ {actor.name} 删除旧背景失败: HTTP {status}"


async def upload_actor_backdrop(actor: ActorInfo, image_path: str | Path) -> tuple[bool, str]:
    _, _, _, _, backdrop_url_0, _ = _generate_server_url(
        {"Name": actor.name, "Id": actor.actor_id, "ServerId": actor.server_id}
    )
    img_path = Path(image_path)
    if not img_path.exists():
        return False, f"❌ 背景图片文件不存在: {image_path}"

    ok, err = await _upload_actor_photo(backdrop_url_0, img_path)
    if ok:
        return True, f"✅ {actor.name} 背景上传成功"
    return False, f"❌ {actor.name} 背景上传失败: {err or '服务器返回空响应'}"


def _normalize_actor_name(name: str) -> str:
    """演员名归一化：NFKC + 去空格 + 小写，用于模糊匹配 GFriends 索引。

    解决全角/半角差异（如 ＨＤ→HD）和空格差异（如 "波多野 結衣" vs "波多野結衣"）。
    """
    normalized = unicodedata.normalize("NFKC", name or "")
    normalized = re.sub(r"\s+", "", normalized).lower()
    return normalized


def _safe_filename(name: str, suffix: str) -> str:
    """生成安全缓存文件名：清洗演员名中的 Windows 非法字符，避免下载/读写失败。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "")
    cleaned = cleaned.strip(" .")
    return (cleaned or "unknown") + suffix


def gfriends_find_actor(gfriends_index: dict[str, str], name: str) -> str | None:
    normalized_name = _normalize_actor_name(name)
    if not normalized_name:
        return None
    for key, url in gfriends_index.items():
        stem = key.rsplit(".", 1)[0] if "." in key else key
        if _normalize_actor_name(stem) == normalized_name:
            return url
    return None


async def from_gfriends(actor: ActorInfo, gfriends_index: dict[str, str], cache_dir: Path) -> str | None:
    url = gfriends_find_actor(gfriends_index, actor.name)
    if not url:
        return None
    local_path = cache_dir / _safe_filename(actor.name, "_gf.jpg")
    if not await download_file_with_filepath(url, local_path, cache_dir):
        return None
    if local_path.exists():
        return str(local_path)
    return None


def _parse_graphis_html(html_text: str, actor_name: str) -> tuple[str, str] | None:
    """从 graphis.ne.jp 页面 HTML 解析演员头像 URL。

    Returns:
        (small_pic_url, big_pic_url) 或 None（未找到）
    """
    html = Selector(html_text)
    src = html.xpath("//div[@class='gp-model-box']/ul/li/a/img/@src").getall()
    names = html.xpath("//li[@class='name-jp']/span/text()").getall()
    if names and actor_name in names:
        idx = names.index(actor_name)
        if idx < len(src):
            small_pic = src[idx]
            big_pic = small_pic.replace("/prof.jpg", "/model.jpg")
            return small_pic, big_pic
    return None


async def from_graphis(actor: ActorInfo, cache_dir: Path) -> tuple[str, str | None] | None:
    from urllib.parse import quote

    local_data = resources.get_actor_data(actor.name)
    jp_name = actor.name
    if local_data.get("has_name"):
        jp_name = local_data.get("jp", actor.name)

    urls = [
        f"https://graphis.ne.jp/monthly/?K={quote(jp_name)}",
        f"https://graphis.ne.jp/monthly/?S=1&K={quote(jp_name)}",
    ]
    for url in urls:
        async with manager.acquire_computed() as computed:
            res, _ = await computed.async_client.get_text(url)
        if res is None:
            continue
        parsed = _parse_graphis_html(res, jp_name)
        if parsed is None:
            continue
        small_pic, big_pic = parsed
        avatar_path = cache_dir / _safe_filename(actor.name, "_graphis.jpg")
        if await download_file_with_filepath(small_pic, avatar_path, cache_dir):
            if avatar_path.exists():
                backdrop_path = cache_dir / _safe_filename(actor.name, "_graphis_bg.jpg")
                backdrop_ok = await download_file_with_filepath(big_pic, backdrop_path, cache_dir)
                backdrop = str(backdrop_path) if backdrop_ok and backdrop_path.exists() else None
                return str(avatar_path), backdrop
    return None


async def from_minnano_image(actor: ActorInfo, cache_dir: Path) -> str | None:
    info = EMbyActressInfo(name=actor.name, server_id="", id="")
    res, _ = await get_minnano_info(info, "")
    if res and hasattr(info, "avatar_url") and info.avatar_url:
        local_path = cache_dir / _safe_filename(actor.name, "_minnano.jpg")
        if await download_file_with_filepath(info.avatar_url, local_path, cache_dir):
            if local_path.exists():
                return str(local_path)
    return None


def from_local_avatar(
    actor: ActorInfo,
    local_avatar_dir: str,
    pre_scanned_index: dict[str, str] | None = None,
) -> str | None:
    if pre_scanned_index is not None:
        return pre_scanned_index.get(actor.name)
    if not local_avatar_dir:
        return None
    avatar_dir = Path(local_avatar_dir)
    if not avatar_dir.exists():
        return None
    for f in avatar_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png") and f.stem == actor.name:
            return str(f)
    return None


def build_local_avatar_index(local_avatar_dir: str) -> dict[str, str]:
    """扫描本地头像目录，构建 {stem: path} 索引。

    供批量预览场景一次性扫描，避免逐演员全树遍历。
    """
    index: dict[str, str] = {}
    if not local_avatar_dir:
        return index
    avatar_dir = Path(local_avatar_dir)
    if not avatar_dir.exists():
        return index
    for f in avatar_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png") and f.stem not in index:
            index[f.stem] = str(f)
    return index


async def fetch_actor_info_from_source(actor: ActorInfo, source: str) -> tuple[bool, str, object]:
    """按指定信息源获取演员信息，返回 (是否命中, 描述, EMbyActressInfo)。

    供数据源测试窗口逐源展示。
    """
    info = EMbyActressInfo(name=actor.name, server_id=actor.server_id, id=actor.actor_id)
    if source == "local":
        local_data = resources.get_actor_data(actor.name)
        if local_data.get("has_name"):
            bio = (local_data.get("bio") or "").strip()
            bd = (local_data.get("birth_date") or "").strip()
            if bio or bd:
                info.overview = bio.replace("\n", "<br/>")
                info.birthday = bd
                if not info.locations:
                    info.locations = ["日本"]
                return (
                    True,
                    f"本地演员库命中（{'简介' if bio else ''}{'+' if bio and bd else ''}{'生日' if bd else ''}）",
                    info,
                )
        return False, "本地演员库未命中", info
    if source == "wiki":
        res_wiki, _ = await search_wiki(info)
        if res_wiki:
            result_wiki, _ = await get_detail(res_wiki, "", info)
            if result_wiki and info.overview:
                return True, f"维基百科命中（简介 {len(info.overview)} 字）", info
        return False, "维基百科未命中", info
    if source == "minnano":
        res, _ = await get_minnano_info(info)
        if res and (info.overview or info.birthday or info.taglines):
            return True, "minnano-av 命中", info
        return False, "minnano-av 未命中", info
    if source == "database":
        db_res = ActressDB.update_actor_info_from_db(info)
        if db_res and (info.overview or info.birthday):
            return True, "本地数据库命中", info
        return False, "本地数据库未命中", info
    return False, f"未知信息源: {source}", info


async def fill_actor_info_from_sources(
    info: EMbyActressInfo,
    *,
    existing_overview: str = "",
    skip_db_if_marker: bool = False,
) -> tuple[dict[str, bool | int], list[str]]:
    """从本地→wiki→minnano→db 链路补全演员信息到 info 对象（原地修改）。

    供内置补全和管理器工具共用。

    Args:
        info: EMbyActressInfo 对象，会被原地修改
        existing_overview: Emby 服务器上已有的 overview（用于判断"数据库补全"标记）
        skip_db_if_marker: 为 True 时，若 overview 含"数据库补全"则跳过 db 查询

    Returns:
        (sources, logs)
        sources: {"local", "local_applied", "wiki", "minnano", "db"}
        logs: 日志列表
    """
    logs: list[str] = []
    local_found = False
    local_birth_set = False
    local_overview = ""

    # 0) 本地演员库命中回填（最优先，离线可用）
    try:
        local_data = resources.get_actor_data(info.name)
        if local_data.get("has_name"):
            bd = (local_data.get("birth_date") or "").strip()
            bio = (local_data.get("bio") or "").strip()
            if bd:
                info.birthday = bd
                info.year = bd[:4]
                local_birth_set = True
            if bio:
                local_overview = bio.replace("\n", "<br/>")
                info.overview = local_overview
                for tag in _extract_bio_tags(bio):
                    if tag not in info.tags:
                        info.tags.append(tag)
            if not info.locations:
                info.locations = ["日本"]
            local_found = True
            msg_local = f"本地库命中: {info.name}"
            if bd:
                msg_local += f", 出生日期 {bd}"
            if bio:
                msg_local += f", 简介 {len(bio)} 字"
            logs.append(msg_local)
    except Exception:
        local_found = False

    wiki_found = False
    minnano_found = False
    db_exist = 0

    # 本地命中且简介非空：完全采用本地数据，跳过外部网络来源
    if not (local_found and local_overview):
        # wiki
        wiki_intro = ""
        res_wiki, msg_wiki = await search_wiki(info)
        logs.append(msg_wiki)
        if res_wiki is not None:
            result_wiki, _ = await get_detail(res_wiki, msg_wiki, info)
            if result_wiki:
                wiki_intro = info.overview or ""
                wiki_found = True

        # minnano
        minnano_ok, msg = await get_minnano_info(info, wiki_intro)
        logs.append(msg)
        if minnano_ok:
            minnano_found = True

        # db（仅当 minnano 和 wiki 均未命中时）
        if manager.config.use_database and not minnano_ok and not wiki_found:
            if skip_db_if_marker and "数据库补全" in existing_overview:
                db_exist = 0
                logs.append(f"{info.name}: 已有数据库信息")
            else:
                db_exist, msg = ActressDB.update_actor_info_from_db(info)
                logs.append(msg)

    sources = {
        "local": local_found,
        "local_applied": local_found and (bool(local_overview) or local_birth_set),
        "wiki": wiki_found,
        "minnano": minnano_found,
        "db": db_exist,
    }
    return sources, logs


async def search_actor_info(actor: ActorInfo, wiki_intro: str = "") -> bool:
    info = EMbyActressInfo(name=actor.name, server_id=actor.server_id, id=actor.actor_id)

    _, _ = await fill_actor_info_from_sources(info)

    if hasattr(info, "dump"):
        data = info.dump() if callable(info.dump) else info.__dict__

        # dump() 返回 Emby/Jellyfin 规范键（PascalCase），兼容小写键兜底
        def _get(*keys, default=None):
            for key in keys:
                if key in data:
                    return data[key]
            return default

        actor.new_overview = _get("Overview", "overview", "new_overview", default="")
        actor.new_taglines = _get("Taglines", "taglines", "new_taglines", default=[])
        actor.new_production_year = _get("ProductionYear", "production_year", "new_production_year", default=None)
        actor.new_premiere_date = _get("PremiereDate", "premiere_date", "new_premiere_date", default="")
        actor.new_production_locations = _get(
            "ProductionLocations", "production_locations", "new_production_locations", default=[]
        )
        actor.new_provider_ids = _get("ProviderIds", "provider_ids", "new_provider_ids", default={})
        if actor.new_overview or actor.new_taglines:
            actor.need_update_info = True
            return True
    return False


async def _sync_actor_async(actor: ActorInfo, sync_type: str = "both") -> tuple[bool, str]:
    logs: list[str] = []

    if sync_type in ("both", "info"):
        if actor.need_update_info:
            try:
                ok, msg = await update_person_info(actor)
                logs.append(msg)
            except Exception as e:
                logs.append(f"❌ {actor.name} 更新信息异常: {e}")
    if sync_type in ("both", "image"):
        if actor.need_update_image:
            try:
                if actor.new_image_path:
                    # 直接覆盖上传 Primary，避免先删后传在上传失败时丢失旧头像
                    ok, msg = await upload_actor_image(actor, actor.new_image_path)
                    logs.append(msg)
                else:
                    ok, msg = await delete_actor_image(actor)
                    logs.append(msg)
            except Exception as e:
                logs.append(f"❌ {actor.name} 头像同步异常: {e}")
        if actor.need_update_backdrop and actor.new_backdrop_path:
            try:
                # 直接覆盖上传 Backdrop/0，失败时保留旧背景
                ok, msg = await upload_actor_backdrop(actor, actor.new_backdrop_path)
                logs.append(msg)
            except Exception as e:
                logs.append(f"❌ {actor.name} 背景同步异常: {e}")

    # 把 delete 失败/skip/异常 视为整体失败 (logs 含 ❌ 或 ⏭️) 以使 UI 标红
    success = not any(("❌" in log or "⏭️" in log) for log in logs) if logs else True
    return success, "\n".join(logs)


def sync_actor(actor: ActorInfo, sync_type: str = "both") -> tuple[bool, str]:
    return executor.run(_sync_actor_async(actor, sync_type))


async def sync_batch_async(
    actors: list[ActorInfo], progress_callback: Callable | None = None, actor_callback: Callable | None = None
) -> tuple[int, int]:
    if not actors:
        return 0, 0
    total = len(actors)
    sem = asyncio.Semaphore(SYNC_CONCURRENCY)
    completed = 0

    async def _one(actor: ActorInfo) -> bool:
        nonlocal completed
        async with sem:
            ok, msg = await _sync_actor_async(actor)
        completed += 1
        if progress_callback:
            progress_callback(completed, total, f"正在同步: {actor.name} ({completed}/{total})")
        if actor_callback:
            actor_callback(actor, ok, msg)
        return ok

    results = await asyncio.gather(*(_one(a) for a in actors))
    success = sum(1 for r in results if r)
    return success, len(results) - success


def sync_batch(
    actors: list[ActorInfo], progress_callback: Callable | None = None, actor_callback: Callable | None = None
) -> tuple[int, int]:
    return executor.run(sync_batch_async(actors, progress_callback, actor_callback))
