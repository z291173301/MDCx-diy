# TODO

状态标签：⬜ 未实现 / 🔶 部分实现（已有基础，见括号说明）/ ✅ 已实现

价值·难度标签：**价值**（解决痛点大小）· **难度**（工作量：低<1天 / 中1-5天 / 高>1周）

按价值排序：P1 优先实现 → P2 核心能力 → P3 按需/低频

## P1 优先实现（解决日常痛点，见效快）

### 1. 刮削缓存三项增强 ⬜（`core/scrape_cache.py`）
- **价值：高**　**难度：中**（3-5 天）
- A. **404 负面缓存**：`ScrapeState` 加 `failure_reason`，`NOT_FOUND` 缓存 7 天期内跳过，其他失败仍走重试
- B. **缓存 key 版本化**：表加 `schema_version` 列，解析逻辑修复后递增版本号让旧 done 自动失效
- C. **字段缺失检测**：`list_incomplete(required_fields)` 找"done 但关键字段为空"的片批量重刮（runtime="0" 视为无效）

价值：站点改版期刮到全空片能补刮；404 片不白等重试；解析修复后缓存不失效。

---

### 2. 视频元数据状态机 + 关键帧截图 ⬜
- **价值：中**　**难度：高**（5-7 天）
- 元数据版本号 + 文件指纹（size+mtime）变更检测，schema 升级自动重扫
- 失败按错误码分级 + `next_retry_time` 退避，避免反复重试同一批失败文件
- 关键帧截图：全站无图时 ffmpeg 截帧选最有意义帧作 poster（国产/素人常用）；配置 `video_screenshot_enabled` 默认关，依赖系统 ffmpeg（非 Python 包）

价值：国产/素人片无封面时自动补海报墙。

---

### 3. 演员别名 + 标签声明式配置 ⬜
- **价值：高**　**难度：中**（2-3 天）
- 配置文件写一行 `河北彩花 = 河北彩伽,河北彩花（河北彩伽）`，新刮削自动把别名替换为规范名
- 标签树形映射 + 父级补全 + 冲突检测；Actor Split 正则拆分 `"演员A (别名B)"`
- 写 NFO 前：规范名进 `<actor>`，原始写法保留到 `<actor><aliases>`
- 重名冲突软合并；已有影片不自动重命名；与 #15 联动（别名作 GFriends 候选名来源）

价值：同一人（河北彩伽/河北彩花）分两个演员、带括弧后缀需手动清的问题，写一行配置即统一。

---

## P2 核心能力升级（提升质量与体验，成本适中）

### 4. 字段级多源合并 ⬜
- **价值：中**　**难度：高**（1-2 周）
- 主站点为基准，缺失字段按配置优先级从备用站点补全；字段权重：标题/番号/封面 > 演员/标签 > 剧情/时长/日期
- 配置 `field_merge_enabled`（默认关）
- 借鉴 mdcz（2026-09-20 调研）：演员简介做**质量评分择优**而非先到先得——`ActorProfileAggregator` 按「结构化字段数 + 描述长度分（≥160字=2 / ≥80=1.5 / ≥1=1）」跨源比较取最优；我们的 `fill_actor_info_from_sources` 目前是源序首个命中即停，短简介会锁死首位源
- 借鉴 amane（sqzw-x 新作，Python+GPLv3 同栈，2026-09-20 已深挖）：其 `aggregate/` 完整闭环 = **全源 raw 保留 + `field_sources` 逐字段来源溯源** → UI 逐字段展示各源值供用户点选 → `compute_merge_updates` 按选择算最终值（重命名字段以 `{source: value}` 带源入库）。另有**波次抓取**（`compute_waves`：同站多点合并成一次请求、字段满足即停、语言感知合并）。我们 #4 设计输入：数据层先落 raw 保留 + field_sources 溯源（为手动干预留接口），自动策略参考 mdcz 质量评分，UI 逐字段选择后置

价值：JavBus 有标题没简介、DMM 有简介但封面低清时自动拼出完整记录。

---

### 5. NFO 兼容性增强 🔶（已有 `nfo.py:get_external_id_tag_name` 基础）
- **价值：中**　**难度：中**（3-5 天）
- identifier resolver：`uniqueid[type]` → `<num>` + 无 type → `<{site}id>` 标签映射（→ Website 枚举）
- **可配置字段映射**：每字段多个候选 XML 路径按序取第一个非空
- 演员/标签别名匹配 + 自动创建开关

价值：从 MetaTube/Jellyfin 等换工具已有 NFO 能读进来。

---

### 6. AI 打标签 ⬜（需用户自备 LLM API Key）
- **价值：中**　**难度：高**（3-5 天）
- 路径①：LLM 从标题+简介提取 ≤5 个标签（`AI-` 前缀），走 `USER_LLM_*` 环境变量
- 路径②：多模态从既有标签池选（`json_schema` strict 输出 + 二次校验），只选不造
- 增量重扫（标签池 hash + 资源 hash 变化才重分析）；写入策略 append/replace/only_empty
- 配套标签模型升级（`AIEnabled`/`AIDescription`/`Sort`/`Hot`）

价值：上千部片按剧情/熟女/户外分类自动完成。

---

### 7. NFO 库管理增强 🔶（基础页已上线 v2.0.6+，见 changelog）
- **价值：中**　**难度：中**（约 2.5 天）
- 基础版已有：三栏布局、字段编辑、封面预览、字段级 diff 预览、批量操作、右键菜单（重新刮削/打开目录/删除 NFO）
- 待做：**海报墙缩略图视图**（`QListWidget.setIconMode()` + 可视范围懒加载同目录 `poster.jpg`）/ **仅可播放过滤**（无视频文件的孤儿 NFO）/ **组合筛选**（演员+年份+标签）

不做：SQLite 全量建库、收藏夹/最近看过。

---

### 8. 配置化刮削器引擎 + 调试器 CLI ⬜
- **价值：中**　**难度：高**（1-2 周）
- 声明式配置（JSON/CSS 选择器）：`file_patterns` 番号正则 + `search` + `sites[]`（url 模板 + selectors + post_processors）
- 后处理管道：regexp/split/absolute_url/filename；`fallback_attributes` 备用属性链（src → data-src）
- 多站点失败策略：priority 依次尝试 + 重试退避
- 调试器 CLI：指定站点+番号跑一遍打印字段/图片/日志，无需反复启动 GUI

价值：站点改版后用户写 10 行 JSON 选择器自救。

---

## P3 按需/低频（小改进随时插队，或定位弱相关可不做）

### 9. JavDB App API 端点扩展 ⬜
- **价值：低**　**难度：中**（2-3 天）
- 优先集成磁力列表 `GET /api/v1/movies/{id}/magnets`，排序 `cnsub > hd > size > files_count`
- 配置 `javdb_app_fetch_magnets`（默认关）；复用现有签名/设备参数
- 借鉴 javdb-cli（FlanChanXwO，MIT，2026-09-20 调研）：其端点地图验证了 App API 覆盖面（磁力/排行/收藏/反图搜均可用），实现本项与 #29 前拿它的接口清单对账我们的签名文档

价值：刮完直接看到磁力链接。与刮削定位略偏。

---

### 10. JavInfoApi 作为可选元数据源 ⬜（需自托管）
- **价值：低**　**难度：中**（3-5 天）
- 新增爬虫 `javinfoapi.py`，番号查询 + 批量 lookup（一次 100 个）+ 演员模糊匹配
- Emby 演员管理器数据源优先级新增 `javinfoapi`；API 不可用时 fallback 其他站点

价值：老玩家自建 JavInfoApi 后本地秒查。受众窄。

---

### 11. 维护预览 + 字段级 diff ⬜（注意：NFO 库管理的字段级 diff 是单文件编辑场景，非本项的全局维护预览）
- **价值：中**　**难度：高**（1-2 周）
- dry-run 预览模式：先算变更展示 diff，确认后执行
- 预设模式：`read_local` / `refresh_data` / `organize_files`；与 #12 联动
- 借鉴 mdcz：`publication/` 的三段结构可直接映射——`createPublicationPlan`（声明式变更计划：moves/artifacts/obsolete/replace 全量化）→ `preflight`（执行前观测目标文件 exists/size/mtime 判冲突）→ 确认后按 plan 执行

价值：批量整理前先看到"哪些会被重命名/改写"再执行。

---

### 12. 操作历史与批量回滚 ⬜
- **价值：中**　**难度：高**（1-2 周）
- 新增 `core/history.py` SQLite 记录 `file_move`/`file_rename`/`nfo_write`/`image_download`/`image_overwrite`
- 批量操作 batch_id 关联，回滚逆向操作；与 #11 联动
- 借鉴 mdcz：`PublicationJournal`（begin(plan)→commit→finish + `listUnfinished()` 启动时恢复）是**预写日志**设计——不只服务回滚，先解决"崩溃/断电后半成品操作的可恢复性"，与我们 #98-2 快照三性质、云盘静默吞写同族但更彻底；实现 #12 时 journal 状态机（planned/committed/finished）建议作为一等公民而非附属日志

价值：批量整理搞乱目录结构一键回滚。

---

### 13. missav 爬虫指纹降级策略 ✅（已实现）
- **价值：低**　**难度：低**（0.5 天）
- 实现：默认指纹池新增 `safari17_2_ios`；`web_async.py` 检测到 CF 挑战页（403/503 + challenge 标记）时强制轮换连接池指纹重试。实测 missav.ai 桌面 Chrome 403 → Safari 手机指纹 200

---

### 14. MOVIE_NUMBER_PATTERNS 专用规则补全 🔶（9 前缀已完成）
- **价值：低**　**难度：低**（1 天）
- ✅ 已完成：`9` 前缀规则（`9ssis01` → `SSIS-001`，编号补零到 3 位，配 7 个测试用例）
- ⬜ 待做：其余（LAF/MISM/MKBD/CWPBD/SM/MCDV）通用规则可兜底，仅在匹配不理想时补充

---

### 15. GFriends 候选名列表匹配 🔶（单名函数 `gfriends_find_actor(gfriends_index, name)` 已存在）
- **价值：中**　**难度：低**（1-2 天）
- 待做：接口改为接受 `names: list[str]` 依次 NFKC 归一化匹配，首个命中即返回；`ActorInfo` 加 `aliases: list[str]`（来源：JavDB / keyword 列 / #3 声明式别名）

---

### 16. 深度链接（`mdcx://` 自定义协议）⬜
- **价值：低**　**难度：中**（1-2 天）
- 协议：`mdcx://scrape?code=ABC-123`、`mdcx://import?path=...`
- 注册：Windows 注册表 / macOS Info.plist / Linux .desktop；已运行时 IPC 传递 URL

价值：TG 群发链接点击直接唤起 mdcx 开刮。

---

### 17. per-scraper 代理配置继承 ⬜
- **价值：中**　**难度：中**（2-3 天）
- `ProxyProfile` 多 profile；`SiteConfig.proxy`: None=继承全局 / `"direct"`=不用 / 其他=指定 profile
- UI：设置 → 代理管理

价值：JavDB 要日本节点、DMM 要别的代理，每站独立配。

---

### 18. 刮削结果自动备份 ⬜（低优先级）
- **价值：低**　**难度：中**（1-2 天）
- 定时 + 变更计数双触发，队列化防重入；拷贝限速；zip 保留最近 N 份

价值：误删 NFO/磁盘故障能找回。与刮削定位弱相关。

---

### 19. 视频指纹去重 ⬜（低优先级）
- **价值：低**　**难度：高**（3-5 天）
- 10 固定位置抽帧 + 64 位 pHash，时长分桶剪枝 + 汉明距离阈值，传递闭包聚类

价值：同一资源的不同压制/改名副本找出来合并。定位偏媒体库，可不做。

---

### 20. amazon 搜索 URL 双重 quote_plus ✅（已实测确认正确，无需改动）
- **价值：低**　**难度：低**（验证类）
- 已实测：`core/amazon.py:1107` 的双重 `quote_plus` 是正确且必要的——Amazon 对 returnUrl 解一层得到内层 `/s?k=`，跳转时 k 值再解一层；单次编码会导致跳转失败直接去首页。当前代码不改。

---

## mdcz 调研新增（2026-09-20，来源 https://github.com/ShotHeadman/mdcz ，GPL-3.0 与我们协议兼容，借鉴设计非翻译代码）

### 21. 图床冷却持久化 + 失败分类 ✅（2026-09-20 实现：core/image_host_cooldown.py，挂 _fetch_image + DMM 剧照下载，7 条测试）
- **价值：高**　**难度：低**（1-2 天）
- 参考 mdcz `cooldown/PersistentCooldownStore` + `ImageHostCooldownTracker`：
  - 失败分类精细化——传输层错误（TLS EOF/超时/连接重置）**不计**图床冷却（是网络问题不是图床问题）；仅可重试 HTTP 类（503/429/408）连续 ≥3 次才把该图床 URL 置入冷却，期内直接跳过该图源候选
  - 冷却状态落盘（json），**重启进程不丢**——我们现有失败记忆均为进程内，重启批刮会重新撞死图床
- 与我们既有纪律同源（"404 不计错误率""真实限流信号只有 403/429/连接异常"），是其图片下载侧落地
- 挂载点：`base/web.py` 图源候选链 + 图片下载失败处理路径

### 22. 爬虫测试 recording 回放框架 ⬜
- **价值：中**　**难度：中**（3-5 天）
- 参考 mdcz `tests/recording/`（record→replay）：真实站点响应录制一次存 fixture，CI/日常测试离线回放
- 解决我们反复踩的"手写 HTML 夹具与真实页面漂移"（#174 生产形态 spec、#176 真实快照两次实证同族问题）；站点改版后重录即可回归
- 起步：先给 top 5 流量站（javbus/javdb/dmm/avmoo/missav）各录 1 个番号样本

### 23. 媒体根路径规范化（rootId + relativePath）⬜
- **价值：中**　**难度：高**（>1 周，侵入面大）
- 参考 mdcz `media-store`：所有文件引用以「媒体根 id + 相对路径」表达，不存绝对路径
- 直击我们云盘映射盘历史坑（Z: 盘 samefile 假阳性/静默吞写/盘符漂移）与 NAS 挂载场景；#12 journal 若实现，plan 里的文件引用应采用同款 ref 结构
- 建议与 #11/#12 合并设计，单独立项避免重复改造

### 24. 演员源补充：avwikidb / jav321 / ppvdatabank ⬜
- **价值：低-中**　**难度：中**（每站 1-2 天）
- mdcz 独有、我们缺失的站点：**avwikidb**（日文演员/作品 wiki，可作演员信息源 wiki/minnano 的兜底）、**jav321**（英文元数据聚合，海外用户向）、**ppvdatabank**（FC2 PPV 结构化库，补我们 fc2/fc2ppvdb 生态）
- fantia/fc2hub 与现有源重叠度高，暂不列
- 新增站点注意全链路影响面：Website 枚举 + 注册 + proxy 列表 + migrations 清洗 + UI 站点列表 + 文档五处数字

### 25. 维护任务代数防陈旧写入 ✅（2026-09-20 实现：演员管理器 _session_gen 代数 + 9 回调守卫 + cancel 即时恢复 UI，6 测试含 AST 哨兵）
- **价值：低**　**难度：低**（0.5 天）
- 参考 mdcz `MaintenanceSession` 的 `StaleMaintenanceGenerationError`：会话携带代数号（generation），后台任务写回时校验代数，过期任务结果直接丢弃
- 与我们「快照比对才清 dirty」（#98-2）同族，补强"用户连续发起两次维护/取消后旧任务回写"场景的防护

---

## 2026-09-20 开源生态调研第二批（amane / javdb-cli / OpenAver / sakuramedia / JavBoss / javinizer-go / missav-api / dmm-proxy-api）

### 26. 低清海报超分增强 ✅（2026-09-20 完整落地：core/super_resolution.py + scraper 挂接 + 下载高清图组 UI 开关 + 9 测试）
- **价值：中**　**难度：中**（2-3 天）
- amane 方案（直接采纳）：**ncnn-vulkan 外部二进制 + 按平台首次使用时下载**（Real-ESRGAN xinntao v0.2.5.0 / waifu2x nihui 20250915 的 GitHub release，darwin/linux/win32 三平台 zip），不随包分发、免 torch/ONNX 大依赖
- 预设制：`realesr-photo-4x`（realesrgan-x4plus 4x 无降噪）/ `waifu-photo-2x`（upconv_7_photo 2x）
- 触发策略（我们侧）：默认关，仅"无高清候选且源图宽度 < 阈值"时对 poster 跑一次；产物按输入 hash 缓存；失败静默降级用原图
- 注意：ncnn-vulkan 需 Vulkan 运行库（Win10+/macOS MoltenVK 随包/Linux 驱动），打包版冒烟必测

### 27. 磁盘监控自动刮削 ⬜（来源 amane，2026-09-20 已深挖，实现路径已定）
- **价值：中**　**难度：中**（3-5 天）
- amane 方案（`scheduler/watcher.py`，watchdog）：Observer + **PollingObserver 回退**（网络盘/云盘 inotify 不可靠必须轮询）+ 3 秒 debounce（创建/删除/目录删除分桶）+ **move 事件 src/dest 配对**（Windows 删除通知不区分文件目录，按前缀处理子树）
- 每库一个 handler 绑定 library_id，事件自带归属免路径前缀反推
- 接入点：事件 → 番号解析 → 复用现有刮削队列；UI「监控中」状态灯 + 配置默认关
- 我们云盘用户占比高，**轮询间隔给配置项**并提示 CPU 代价

### 28. 演员资料卡墙 ⬜（来源 OpenAver，MIT 可移植）
- **价值：中**　**难度：高**（1-2 周）
- 演员作为一等实体：资料卡（头像+姓名+别名+作品数）、按罩杯/身高/年龄/作品数排序、跨语言别名归一（与 #3 联动）
- 数据基础我们已有（actor_db 中文/日文/keyword 列、minnano 身材字段）；缺的是聚合展示层与排序索引

### 29. javdb_app 端点扩展：反图搜 + 收藏列表 ⬜（来源 FlanChanXwO/javdb-cli，MIT）
- **价值：中**　**难度：中**（每端点 1-2 天）
- javdb-cli 的 App API 端点地图远超我们 javdb_app 已用的搜索+详情：**反向图搜**（截图找番号，全新能力）、TOP250/排行榜、用户想看/看过列表、磁力（→ #9）
- 落地前先按其 README 端点清单与我们的 `docs/JAVDB_APP_SIGNATURE.md` 对账，验证签名/设备参数是否同套
- 反图搜可做"右键封面 → 识别番号"入口，补 FC2/素人无番号场景

### 30. 图片/视频 URL 存在性探测用 Range 加速 ✅（2026-09-20 实现：_validate_dmm_image_url Range+stream 探测，3 条回归测试）
- **价值：中**　**难度：低**（0.5-1 天）
- `Range: bytes=0-1023` 请求接受 200/206/416 判存在，实测把探测从 37s 降到 ~1s（不等全响应体）
- 可用于 `check_url` 图床候选逐链接探测（当前逐候选完整请求，15+ 条各 3.6s 是已知慢点——测试耗时诊断记忆同源）

### 其他调研结论（不立项）
- **sakuramedia**（Dart NAS 平台）：订阅下载自动入库与刮削器定位偏离；其"采样指纹去重"（少量字节片段识别重复媒体）印证 #19 方案可行，实现 #19 时可参考其 wiki 算法文
- **JavBoss**（Go 一站式）：主打内置播放器+磁力下载链路，与我们 NFO 生成定位偏离，不跟进
- **javinizer-go**（Go）：CLI/TUI/REST 多形态工程印证 #8 调试器 CLI 方向，无独立新点
- **unofficial-api-for-missav**（2026-09-20 已深挖）：许可实为 **AGPL-3.0**（GitHub 识别失败才显示 NOASSERTION）——开源，站点知识可学，但 AGPL↔GPLv3 双向不兼容，代码行不可复制。核心情报逐一核对后**我们已全覆盖**：Recombee API+HMAC-SHA1 签名（我们 missav_api.py 同源实现）、`safari17_2_ios` 指纹必用（TODO #13 已实现且结论一致——桌面 Chrome 403/iOS Safari 200）、OG meta 字段提取+多候选回退（我们 missav.py 已有 actor/director/date/duration/tags 链）。知识增量存档备查：①视频 CDN 为 **surrit.com**（`surrit.com/{uuid}/playlist.m3u8`，m3u8 基址藏在 JS 里按 `|` 分段逆序拼接，三级回退：packed JS→直链→surrit）——未来若做"预览播放集成"走此路；②parser 用 selectolax（比 parsel 快，我们万级批量解析非瓶颈，不跟进）；③"Request blocked → 换 impersonate + 下载并发降 1"与我们的 CF 指纹轮换互证
- **amane 的 AI 助理/AI_POLICY**：与 #6 AI 打标签同方向但更大（自然语言操作全库），待 amane 深挖后再决定是否升级为独立条目
- **OpenAver 的 AI-operable REST API + capabilities manifest**：桌面端暴露本地 REST 接口供 AI 工具操作，属产品路线决策，暂存档

### 31. AI 生成 Issue 治理政策 ✅（2026-09-20 实现：.github/AI_POLICY.md + 双模板披露下拉 + README 贡献小节）
- **价值：高**　**难度：低**（0.5 天）
- amane 有完整《AI 使用政策》：允许 AI 辅助写 issue，但必须 ①提交者本人 review ②按三档披露 AI 使用程度（部分润色/主要撰写/Agent 自动化创建）③明确警告"AI 代码分析能力与文本表达能力不匹配，不得原样提交"
- 直击我们当前痛点：z291173301 系列的 AI 包装式诉求（模板措辞、推断当事实、#178 辱骂+命令式）——政策文件给"不采纳"提供公开、对事不对人的裁决依据
- 落地：起草 `.github/AI_POLICY.md` 简化版 + issue 模板加披露勾选项 + CONTRIBUTION 指引链接

### 32. 插件化扩展源体系（战略存档，不急做）
- **价值：中（长期）**　**难度：高**
- amane `plugins/`：capability 化 `SourceDescriptor`（frozen+serializable）+ drop-in 目录发现，社区可写插件扩展站点而核心不改码；README 以 `eco:project` 标签经营社区生态
- 我们 36 站内置注册表模式短期够用；若未来站点维护成本成为主要负担（站点改版潮），插件化是出路。届时参考其 profile() 推导站点角色的设计（与"权威声明源"纪律同根）

### 33. amane 独有站点补录候选 ⬜
- **价值：低**　**难度：中**（每站 1-2 天）
- amane 26 站中我们缺 5 个：**jav321**（与 #24 重复计）、fc2club（FC2 第三方）、kin8（KIN8 软)、giga（ARIOL/GIGA）、wp_works（W16 系）——后四个为小厂牌官网，我们 official 路由未覆盖时才有价值，先核对 official 30 家清单再定
