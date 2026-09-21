# 用户指令记忆

本文件只记录长期有效的行为规范、构建发布流程、排错方法和环境约束。项目实现细节以代码、测试和文档为准。

## 协作与质量

- Date: 2026-09-02（持续更新）
- Category: 工作流协作
- Instructions:
  - 简体中文回复；面向小白说明按"现象和影响 → 原因 → 可执行步骤"组织；日期时间一律用北京时间 (UTC+8) 表述并显式注明。
  - **本记忆文件不受 150 行长度限制**（用户 2026-09-06 明示覆盖系统规则）；但入选标准不变——只记"以后每次都该怎么做"，不记单次任务细节、不记读代码即可获知的内容。
  - **遇到可记内容时主动记入本文件**（用户 2026-09-06 明确要求），不等提醒；每次任务收尾时自问"本轮有没有值得沉淀的行为模式/流程纪律/环境陷阱"，有则立即写入并合规自检。
  - 改动前说明内容与原因；用户明确要求提交/推送后才执行，绝不擅自操作。直接在当前分支操作。
  - **检查纪律**：每次代码改动后跑 `uv run quick-check`；提交前跑 `uv run check --skip-hook-install`。仅改 `docs/*.md` 或本文件时只需 `git diff --check`。全绿判定 = 退出码 0 + `grep -E "\.py:[0-9]+: error|Found [0-9]+ error"` 无输出（CI 连挂三次的教训：mypy 输出可被 tail 截断）。**`ruff check` 过 ≠ `ruff format --check` 过——改 .py 必须 `ruff format` 落地**（2026-09-09 四次 CI 挂全是 format 漂移）。`scripts/` 也在 check 范围。**pre-push 钩子三注意（2026-09-20 实证）**：①`hooksPath` 是本地 git config，**环境重置后静默失效**——而日常跑的 `check --skip-hook-install` 恰恰跳过自动配置钩子那步，两者叠加导致失效无人察觉；重置后先 `git config core.hooksPath` 验证，为空则 `git config core.hooksPath .githooks` 补配置；②钩子生效时日常流程=「quick-check → commit → push（钩子自动全量）」，**不要再手动多跑一次全量**（省 3 分钟）；③钩子失效期间推送前必须手动全量 check 兜底；④`.pre-commit-config.yaml` 为历史遗留、不依赖。
  - 提交前必看 `git status` 未跟踪文件：运行残留与中间产物不得 `git add -A` 入库，先 `.gitignore` 排除。
  - **提交信息不要手写 Co-authored-by trailer**：`prepare-commit-msg` 署名 hook 每次 commit/amend 从 git config 的 `coauthor.*` 无条件追加，手写会重复。**hooksPath 双向坑（2026-09-20 实证）**：署名 hook 原在 `.git/hooks/`（不在 .githooks/），一旦 `git config core.hooksPath .githooks` 启用 pre-push，`.git/hooks` 下全部 hook 即被重定向**静默失效**（当天连丢 3 个提交的署名才发现）——现已把 prepare-commit-msg 收编进 `.githooks/` 入库（b1a726a0），两 hook 同目录自洽；环境重置后补一条 `git config core.hooksPath .githooks` 即同时恢复署名与推送自检。
  - **"本地全绿≠CI 通过"三维度**：输出截断 / 版本语义差异（模块级带值注解 3.13 立即求值 vs 3.14 PEP 649 延迟，单例声明一律无注解赋值）/ 平台差异。Windows runner 两坑：①`subprocess.run(text=True)` 一律显式 `encoding="utf-8", errors="replace"`（默认 GBK 遇 UTF-8 字节炸链）；②**glob 模式里 `[XX]` 是字符类不是字面量**，而 Windows 下 `pathlib.glob` 默认大小写不敏感（`pathlib/__init__.py`: 非 posix 即 case_sensitive=False），`glob("*[SR]*")` 会命中 `poster.jpg`（含小写 s）造成假红——断言"临时产物已清理"一律用 `[p for p in tmp_path.rglob("*") if "[SR]" in p.name]` 形式，别用 glob 通配符。
  - **changelog/版本纪律**：提交前更新 `docs/changelog.md` 当前版本条目（版本号归属用户，不擅自开新段）；写法=用户视角发布说明（留议题号/现象/结果，删排查叙事与哈希）。版本同步用 `scripts/bump.py --version <YYYYMMDD> --name <X.Y.Z>`，`bump.py --check` 与 `tests/test_version_consistency.py` 兜底。**"已发版"判据 = 数字 tag 已推送（`git ls-remote --tags origin`），不是 changelog 有没有该段**；当前版本未发版时被后续议题取代的条目要合并重写成最终形态。
  - 站点/爬虫/配置改动同步检查：UI 文案、README、docs、爬虫总数（`get_registered_crawler_sites()`）、**`config/migrations.py` 旧值清洗**（漏迁移 → pydantic 校验失败 → "保存不生效"）。
  - **写死数字前 grep 代码核实**。高频漂移锚点：默认网站源顺序、代理域名列表、命名变量表、设置 Tab 名、字段优先级数、演员库列、指纹池、主窗口行数。README 爬虫数四处同步 + FEATURES.md 标题是独立第五处。**Wiki 维护纪律**：`wiki/` 目录是 GitHub Wiki 内容源；每次回帖议题后把通用答案回填 FAQ；Wiki 仓库需用户先网页建首页才能克隆。
  - **长时间任务标准做法**：① background_terminal 后台终端；② checkpoint 断点续传（state 落盘，后台终端 1 小时上限连 wrapper 一起回收，checkpoint 是唯一恢复手段）；③ 分批处理批间落盘；④ wrapper 45-50 分钟自重启；⑤ 进度看落盘文件不看终端日志（stdout 全缓冲可能 0 字节假象）。
  - **功能移除类需求先调研证据再答**：查活跃度、底层共享依赖、移除成本；用户转述与代码证据矛盾时以代码为准。

## 议题定性与证据

- Date: 2026-09-02（持续更新）
- Category: 排错调试
- Instructions:
  - **定性前必须看完用户附上的全部证据，尤其每张截图**（#73 教训：日志文本不是证据全集，未看的截图里就有真 bug）。定性"用户误读"=断言无 bug，错判代价大，须额外谨慎。
  - **报错消息先验证是"真失败"还是"通知被误伤"**（#69：迁移警告被当校验失败）；消息产生端与消费端各自都要查。
  - **版本定性用状态区指纹（配置文件名 + MDCx 数值版本）+ 日志/UI 特征串反推构建落点，不信用户自报版本号**（#151/#168 两实证）；先判"待修复"还是"已修复待发版"，避免给已解决问题重复动刀。
  - **「关工具窗连带主进程退出」两条独立根因按触发条件分诊**（#159/#175）：①主窗藏托盘后关最后可见窗 → `quitOnLastWindowClosed`；②取数中关 `WA_DeleteOnClose` 对话框 → 工作线程未等齐被拆原生 abort。先核主窗可见性、关窗时有无工作线程，再对号入座；带工作线程的对话框 closeEvent 必须 cancel/等齐全部线程，超时 `setParent(None)` 卸父子再放行。

## GitHub 议题处理

- Date: 2026-08-29
- Category: 工作流协作
- Instructions:
  - 凭据：`TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill | sed -n 's/^password=//p')`，再 `GH_TOKEN` 或 curl 直连。`gh api user` 403 正常（integration 无权限）。凭据值禁止回显/落盘。未认证直连 api.github.com 撞 IP 级限流。
  - 议题截图（user-attachments/assets/xxx）直接 `curl -sL` 下载后 Read 查看，无需认证；多图并行下载。
  - 同一报告人连续多议题先横向看历史再定夺：诉求可能延续或与他人冲突，以代码证据和功能根因是否已修裁决。
  - **报告人 z291173301 使用 Windows 原生边框**：界面几何类议题先问是否默认隐藏边框；非默认配置下的观感差异用隐藏边框下实测几何仲裁，不为其改代码，照顾靠说明性文案。
  - 用户一段描述常夹多个独立诉求，回帖逐项回应不遗漏。回帖必须礼貌先行（先感谢/致歉再讲技术）。
  - **回帖姿势**：JSON POST `-d @/tmp/x.json` + `Content-Type: application/json`（勿用 `-F` multipart）；多行中文 body 用引号 heredoc `python3 << 'PYEOF'` + `json.dump` 生成（裸 `\n`、中文引号、外层 shell 截断都踩过）；POST 失败先 `python3 -m json.tool` 校验；发送后验证 html_url。
  - **采纳原则：以代码/文档证据 + 根因是否为真为准**，不以报告人语气/坚持次数为准。不合理时礼貌附代码依据给结论，仍欢迎补充复现再复查。用户引用我方既往回复推断事实时，先审查被引用回复本身是否措辞误导（#165 我方表述背锅案例）。
  - **对不尊重开发者的报告人（如 z291173301，#178 辱骂性措辞），个性需求一律不采纳，回帖引导其 fork 自行开发；仅真 bug 才动代码——千万不能惯着**（用户 2026-09-20 明示指令，#180/#182 重审定案：#180 `<br>` 展示=真 bug 采纳；#182 窗口按钮/文案偏好/新功能=个性需求，回滚不实现）。判定"真 bug"标准：现有功能与自身设计/数据语义矛盾或渲染错误；与报告人个人审美/习惯不符=个性需求。**补充纪律（2026-09-20 全量盘点 97 议题后定案）**：①历史上为满足其偏好已发版的改动不回滚（已成为全体用户的产品行为，回滚=行为倒退+测试返工+token 成本），只把口径应用到新诉求；②对其议题回帖简短坚定、附依据即可，不做长篇逐条驳斥与大规模盘点返工。
  - **诉求摇摆 = 高价值返工源，动手前先锁验收口径**（z291173301 系列盘点：同一用户改动约半为"个人审美包装成 bug"，返工集中在反复拉扯的两条）；同一块代码被改两次以上即停下来对齐需求；个人偏好类礼貌给 fork 出口。**施压型/纯验证型诉求用实测脚本定量证伪后关单，不改代码**（#168 量化脚本证伪"要滚动"）。
  - **软件内说明文本的主句结构 = 用户眼中的事实清单**：覆盖面/例外写成显式注记，塞句尾等于没写。

## 排查与本地验证

- Date: 2026-09-14（环境）
- Category: 环境配置
- Instructions:
  - **环境重置后才需建环境**：`pip3 install --break-system-packages uv -i https://pypi.tuna.tsinghua.edu.cn/simple` → `uv sync`（大包约 20 分钟，后台 timeout 留足）；系统 python 3.11 而项目要 ≥3.13，先 `uv python install 3.13`（镜像 `UV_PYTHON_INSTALL_MIRROR=https://ghproxy.net/...`）；uv 侧 `export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。PyQt6 测试前装 ci.yaml 的 apt 列表（先 `apt-get update`），必须 `QT_QPA_PLATFORM=offscreen`。
  - **background_terminal 用 sh（dash）**：脚本含 `[[ ]]` 会空转，一律 POSIX 语法或 `bash -c` 包装；后台终端内 `git credential fill` 拿不到凭据，token 在前台 bash 获取。

- Date: 2026-09-02（持续更新）
- Category: 排错调试
- Instructions:
  - **仓库根 `config.json` 是脏配置**，验证配置/网络栈用 `Config()` 默认配置写临时文件再指 `manager.path`。
  - **行为修复流程：先复现测试跑红 → 修 → 绿 → 反向验证**（修复前代码喂测试确认转红防恒真）；修复后反向审查边界与调用点语义。pytest-asyncio strict 标记纪律：文件内**全是** async 测试才用顶部 `pytestmark = pytest.mark.asyncio`；混有同步测试时必须逐函数加 `@pytest.mark.asyncio`，文件级 pytestmark 会给同步函数误打标记刷满 `PytestWarning`。
  - **测试必须喂生产形态数据，不手造**（#174/#176 同族两实证）：回标链路 spec 的 `name`/`site`/`group` 从生产构建函数取或逐项对齐；网页解析夹具用真实页面快照入 `tests/fixtures/`（手写 HTML 在 get_text 拼接细节上漂移造成假红/假绿；真实快照还能锁模板级共性结构）。
  - 结构约束类修复用 **AST 哨兵**锁位置（await 找 `ast.Await` 包装节点，无 `node.await`）；写完拿修复前代码反向喂哨兵确认判失败。
  - **conftest dummy 双陷阱**：①实例属性遮蔽真实 manager 属性；②真实 manager 加新方法必须同步给 dummy 加同名（缺了在 Qt 测试以 qFatal abort 形态爆）。绕 dummy 用独立 `uv run python` 脚本或 AST 哨兵。
  - **subagent 结论不可直接采信**（实证 22 项宣称 11 项编造/夸大）：要求输出"已排除假设清单+理由"，修复前每条独立复现脚本重现；典型证伪形态是断言的崩溃阈值/触发条件与实测不符。外部 AI 的源码分析结论同样独立验证后采纳。
  - **数据质量治理前必须实证全量抽查，不纯代码推断**（dmm_cid_routes 差点误删 10.6% 真实系列）；测试锁定 bug 时以文档语义（UI 名称/description/docstring 三处一致）为仲裁。
  - 触发链每一环落实："功能上可达"≠"正常路径会走到"（FC2 分类链案例）；泄漏/累积类先写最小复现脚本量化（gc/任务计数+对照实验）。
  - **CancelledError 在 task 收集点与 Exception 同级软着陆**（单项记 CANCELLED 继续跑），整轮取消只由 cancel_event 分支负责；`wait_for` 的 cancel 残留不得穿透。
  - **误判家族（同族四条，反复出现）**：①「未命中/空结果」是业务常态，不得与「通道故障」混同一跳过标记（#90 failed 集、404 不计错误率、外部探测先分类错误构成再设阈值——真实限流信号只有 403/429/连接异常）；②模型哨兵默认值（"0000-00-00"）是 truthy 会被放行，下发前必须按格式白名单归一化（#126/#145 宽松日期解析：支持 `- / .`、年月日、紧凑 `YYYYMMDD`，补零后交 `date()` 真校验）；③集合合并类 bug 第一嫌疑永远是有无 dedupe（#100）；④"不标来源就报结果"的日志/汇总字段是诊断盲区，把决策上下文（host/失败分类）带出来（#100-③/#101）。
  - **Emby/Jellyfin 同步字段完整性**：`UpdateItem` 对缺省 `Genres`/`Tags`/`ProviderIds` 空引用（上游 #17366），payload 三者恒非 null（无新值回填旧值、ProviderIds 合并丢空）；日期/年份归一化收口在模型层 `dump()`/`update_person_info` 单一出口。
  - **持久化快照三必备**（#98-2）：原子写（tmp+`os.replace`）/ 按快照比对才清 dirty / 停止退出路径强制落盘；子集续跑不得拿子集做全库清理。传 `Flags` 全局列表一律 `list(...)` 快照。
  - **「日志说成功但文件不存在」第一嫌疑 = 写后无回验**（映射云盘 `os.replace` 静默吞写实测）：replace 后 exists 校验、不过退直写、仍不落抛错；同盘历史坑（samefile 假阳性→复制用 `shutil.copyfileobj`、PermissionError 瞬时占用）防御模板在 `_copy_file_atomic_sync`/`write_file_atomic`。
  - **WinError 123 先实测真实失败串再归因**：常是云盘/映射盘文件名**长度**超限而非非法字符（115/夸克）；出路是降 `folder_name_max` 或先刮本地盘，不硬改截断。命名截断的目录级字段用「稳定预算」（只由模板结构+最大长度决定），回归须两部同系列对比一级目录一致；番号识别第一锚点是日志 `[number]` 行。
  - **「开关组合不生效」先画清每个开关的写入端/读取端**（#98-1：三开关写入端各自写、读取端共用单闸门）；结果标记行留 log 通道恒出，过程明细进 web 通道。
  - **测试耗时诊断**：`--durations=15` 找头 → 单测 profile → 打桩计数。两大真凶=生产节流逻辑真实 sleep（autouse patch）与单测偷跑网络（conftest 断网桩）。删文件省不了几秒，修慢点收益 10 倍。
  - **大范围撤回**用 `git revert --no-commit <多提交>` 合并单撤销提交。**同域测试文件归一**：哨兵被真实行为测试覆盖即删，同 fixture 复现测试并入主回归文件。
  - **TRAWL/分发包**：打包布局与启动脚本入口假设必须端到端对账（发版前用解压后真实布局实测一次启动）；bun 按 cwd 安装依赖、模块解析穿透父级 node_modules。大 zip 远程验尸用 HTTP Range 读中央目录（HEAD 对 S3 可能返回 0，用 GET+Range）。
  - cmd bat 在 chcp 65001 下中文注释字节错位，注释用 rem 且避开特殊结构。ruff B010/B009 与 mypy attr-defined 组合：动态属性 `obj._x = v  # type: ignore[attr-defined] # noqa: SLF001` + `getattr(obj, "_x", None)  # noqa: B009`。
  - **还原/回程类回归用「三态对比探针」定性**（fresh→最大化→还原并排 dump 几何）；探针复现 UI 几何时不得 stub QSS（字体度量偏小缺陷不复现，须挂真实样式）。

## 并发与网络库行为

- Date: 2026-08-29
- Category: 排错调试
- Instructions:
  - **curl_cffi 0.16 流式关闭 = `quit_now.set()` + `await aclose()`**（单独 aclose 拉满响应体阻塞；单独 close 把 handle 置 None 后 cleanup 抛 TypeError）。**同步 close() 只是发起 abort**：内部 `perform()` 任务仍 pending，"Task was destroyed" 第一怀疑 = 某 cancel 没人消费/某内部任务没人等（shield 模式 cancel 后必须 gather）。
  - **内网/自建服务（Emby/Jellyfin）不得复用爬虫重型指纹栈**（#133：只对 127.0.0.1 免检，真实内网 host 全被拖慢）：控制面 API 用轻量 httpx 直连客户端（无指纹/无池/无限流/显式超时），返回形状对齐 `(data, error)` 便于渐进切换；这类接口补耗时/失败日志。
  - **「取消打断收尾」租约泄漏定性法**：看双客户端是否同时残留（双残留=Computed 级，单残留=CrawlerProvider 级）；防护三件套 = 释放路径 shield / try-finally 恒释放 / 逐实例 suppress；**`__exit__` 与 `__aexit__` 成对入口要成对审计**（#55 只修同步侧的教训）。
  - **LogBuffer 任务树归因**：写入按 `_ROOT` contextvar，`process_one_file` 入口 `new_root()` 切断兄弟继承。
  - **后台线程跑异步一律走全局 `AsyncBackgroundExecutor`**（app 持久后台循环），禁用 QThread 内自建一次性 loop（curl_cffi 定时器注册到死 loop → Windows 弹 "Python-CFFI error"）；AST 哨兵锁方法内不得出现 `new_event_loop`/`run_until_complete`。
  - 并发范式：文件间 `asyncio.wait(FIRST_COMPLETED)` 滑动窗口，文件内多站点 `gather`；后台协程统一 `utils/qt_thread.py::run_in_background`，结果经 Qt signal 回主线程，新增后跑 `scripts/check_thread_safety.py`。
  - 出厂模板在 `resources/userdata/`，运行时数据在 `manager.data_folder/userdata/`；devbox 代理 127.0.0.1:7890 可能无进程，排查网络时临时关闭代理。
  - **asyncio 线程池归属**：`AsyncBackgroundExecutor` 的 default executor 与主 loop 是两个池，"嵌套 to_thread 死锁"先实测两池是否同一个。

## UI 开发与排错

- Date: 2026-09-02（持续更新）
- Category: UI 开发与排查
- Instructions:
  - **改 UI 先改 `.ui`（唯一权威源）** → devbox 用 `uv run python -m PyQt6.uic.pyuic`（相对路径）+ `scripts/fix_qt_enums.py` + `ruff format` → `tests/test_ui_structure.py`。禁手工改 MDCx.py。
  - **Qt 布局问题先写最小复现脚本验证 Qt 原生行为，再动项目代码**（#72/#74 教训：根因是 BoxLayout 缺末尾 Expanding spacer 而非 setVisible）；`setVisible(False)` 管不到 QSpacerItem 等非 widget 占位项，新加隐藏开关时审计布局全部非 widget 项。
  - **`WA_DeleteOnClose` 对话框带工作线程时 closeEvent 必须等齐**（见议题定性节 #175 条）。
  - **绝对定位同步军规（一族，#62/#66/#68/#82/#117/#123/#144 实证汇总）**：①`setGeometry` 不触发子组件 resizeEvent（须 `resize()`）；QStackedWidget 只 resize 当前页，`currentChanged` 统一同步且**先 resize 所有 pages 再算内部几何**；②容器几何变化后内部布局必须显式 `invalidate()+activate()`；③平移/拉伸一律「设计基准坐标+extra」固定公式、双向幂等（负 extra 即缩回），增量平移会累积漂移；④**同步清单与设计器控件清单一一对账**（先 grep 设计坐标穷举同族行/同组控件，不只补报错那个）；⑤min 尺寸：宽用 `layout.minimumSize()`（硬最小）、高用 `layout.sizeHint()`，**禁取自膨胀后的 childrenRect**；⑥底部余量是按页属性（设置页浮框侵入 63→默认 72，无遮挡页收紧），由 `set_content_bottom_margin()` 覆盖；⑦.ui 残留 maximumSize 会静默夹断自定义拉伸（删节点重编译）；⑧改公式前 grep 公式变量名全 `tests/` 同步既有断言，同顶控件用同一 y，增长量按当前几何实时 floor 算。
  - **重影/重叠先问「渲染层还是定义层」**（#123）：定义层同 cell 多控件几何检测抓不到（`itemAtPosition` 只返回最后一个、包围盒假绿），须 .ui 文本级哨兵（插行后 grep 该 grid 全部 `<item row=` 确认严格递增）；实机截图是最终判据，与 offscreen 矛盾时以实机为准。抬高某行后同滚动内容兄弟 groupBox 与滚动容器高度按设计 y 顺序整体下移。
  - **窗口状态汇聚点审计**（#79/#82/#132）：修窗口联动 bug 时 grep `setWindowState|activateWindow|showNormal|show|hide` 全库枚举汇聚点逐一加「可见且未最小化」守卫；配置保存/加载等业务函数不得顺手操控窗口状态；托盘隐藏后 eventFilter 不得自动 `show()`。
  - **PyQt6 测试纪律**：每个含 Qt 的测试文件顶部（PyQt6 导入前）自持 `os.environ.setdefault("QT_QPA_PLATFORM","offscreen")`；fixture 构造后立即停全部 QTimer；qFatal abort（栈无 Python 行号）查 QTimer 槽与 dummy 桩缺方法。Qt 同名 API 重载签名不同，改前确认目标类签名；测试桩显式枚举属性方法。隔离配置目录用 `monkeypatch.chdir(tmp_path)`，勿把 dummy 的 Path 属性改 str。
  - 主窗口全局绝对定位：长文本 QLabel 用 wordWrap 查 sizeHint；新增顶层控件纳入 resizeEvent 手动几何同步。QComboBox 装饰后缀：`addItem(icon, 文本, UserRole 纯值)`，消费点统一 `currentData()`，信号 handler 收文本须剥后缀。

## 站点与网络

- Date: 2026-08-27
- Category: 排错调试
- Instructions:
  - 各站探测番号与收录依据见爬虫类注释；javdb 仅搜 FC2 需要 Cookie。
  - 站点 API 坑：missav_api Recombee 仅 POST；DMM Affiliate v3 必需 site/service/floor 且 keyword 用 content_id 形态；madouqu 域名动态维护（24h 缓存）；madou_club 番号无横杠；parsel Selector.get() 纯 JSON 返回 dict，解析兼容 str/dict/Selector 三态。
  - 站点增删史：2026-08 删 15 站（48→33），后增 javfree/aventertainments/madou_club、getchu_dmm 并入 getchu、7mmtv 回归；**当前注册爬虫 36**（FEATURES.md 同步）。数字开头模块名（7mmtv.py）用 `importlib.import_module` 加载。
  - 无码官网五站由 official_uncensored.py 统一路由，均需代理；1pondo/pacopacomama/10musume 的 dyn/phpauto JSON API 直通。
  - 被墙站测试：`uv run python -m scripts.dev_proxy start|status|test <url>|stop`；日本 IP 限制站 `--port 7891 --regions "jp|日本"`。devbox 限制：超时≠站点死亡；连通性验证必须 curl_cffi impersonate；批量探测校验 data.title 防假阳性。
  - **HTTP 4xx/5xx 错误串必须携带截断响应体**（#88：Emby 400 根因在 body JSON），保留 `"HTTP {status}"` 前缀不破坏匹配；定位顺序先看客户端实际发了什么。
  - **番号归一化：前导单数字双重语义**（studio 名单数字保留 vs DMM 预约版 `9` 前缀剥掉），改正则前 grep 全部分支、改后跑相邻语义既有测试防双向误伤（#84）。
  - **站点域名优先级/删站属产品取舍，查证给方案不擅动**（#85：域名顺序常有实测依据注释）；删站影响面 = 注册表 + Website 枚举 + 默认 proxy 列表 + migrations.py 清洗 + UI 列表；单站死活须真机实测，别用 devbox 结果判定。
  - **javdb 系三源**：javdb（网页）/javdb_api（镜像站）/javdb_app（App API 免 CF 最稳）；**thejavdb_api 与 javdb 无关**。App 签名机制见 `docs/JAVDB_APP_SIGNATURE.md`，排障锚点=三主机同时 4xx 或 InvalidSignature；环境变量 `MDCX_JAVDB_APP_SIG_*` 免改码覆盖；搜索 limit≤50、分页须 `movie_sort_by=release`。
  - **javdb 图源无水印体系**：`tp.spfcas.com` App 专用（单字节 XOR，首字节 key）vs `c0.jdbstatic.com` 带水印；解密/变换集中在 `base/web.py`；加密流尺寸探测 (0,0) 属预期勿当图失效。

## Windows 打包与发布

- Date: 2026-08-24
- Category: 环境配置
- Instructions:
  - **只有「字符串动态导入」（`importlib.import_module`/`__import__`）才必须显式 --hidden-import**；函数体内静态 import 会被正常收集。`tests/test_build_hidden_imports.py` 哨兵锁定全仓动态导入 ⊆ hidden-import；CI Windows job 有 PyInstaller 冒烟。
  - **一体包硬失败后，所有调用 `scripts/build.py` 的 Windows/Linux 工作流必须先 `fetch_sr_tools`**（2026-09-21 `b077b07c` 实证）：Code Quality 与 Windows 测试全绿，冒烟步因缺 `build/sr_tools` 直接 BuildError。`ci.yaml` 冒烟、`build-windows.yml`、`build-linux.yml` 与 `release.yml` 是四条独立打包入口，改 fail-closed 时四条一起补「cache + fetch 再 build」；`tests/test_sr_bundling.py::test_packaging_workflows_fetch_sr_tools_before_build` 锁顺序。
  - EXCLUDED_MODULES 中 rich/typer 只供构建/CLI；Windows curl_cffi.libs 需显式 --add-binary。
  - **GitHub Actions 两坑**：①runner 标签会整体下线（macos-13 已关闭，Intel 接替 `macos-15-intel`，2027 秋全退役）；②`astral-sh/setup-uv` 无裸主版本浮动标签（写 @v10 报错，须全版本号）。
  - **Release 发版**：推纯数字 tag 触发 release.yml（macOS aarch64/x86_64 + Windows + Linux 矩阵，正文自动取 changelog 当前段）；产物名 `MDCx-<tag>-<平台>-<arch>-<完整40位sha>.<exe|dmg>`，macOS DMG 按架构命名；Windows zip 版走 `package-trawl.yml` 单独管道。发版前确认 consts.py LOCAL_VERSION/VERSION_NAME 与 changelog 一致（bump.py 见上）。

## 日亚 ASIN 数据库与校验方法论

- Date: 2026-09-02（治理工程收官重组）
- Category: 排错调试
- Instructions:
  - **证据强度排序**：tenhow cid 结构化映射 > EAN/JAN 条码 > 标题 NFKC 系列互含 > 图像相似度（重压缩分数带重叠，只能兜底；`_cover_similarity` 三阈值 0.82/0.86/0.70）。
  - **软校验架构**：免验/必验按发现路径分流——条码/EAN=hard 免验、ASIN 库命中=信任免验、软匹配=v2 三步链必验（cid 旁证→标题门+真合集词一票否决（BEST/コンプリート/N時間；特典/限定版不否决）→图像兜底）；入库时序延迟到采信点；演员名兜底是错挂重灾区全链必走。出厂库权威合并：同番号覆盖用户库 5 列、用户独有保留、出厂独有追加。
  - **待修正 sheet 三分类**：①主表已有番号一致→残留直接删；②番号不同→主表番号反查标题比对裁决；③主表未有→标题法/cid 反查裁决。批量行先按 ASIN 去重再分类。
  - **列写入走显式列号映射/查表，不手数 index**（注记写进 ASIN 列污染 9 行实证）；入库后 sanity check `r[1]` 应是纯 ASIN。出厂库更新仍须用户明确确认。
  - **评估库存价值先问"生产会不会走到那一步"**；裁决图遍历全部候选取最高分（同番号 digital/mono 双封面并存为真）；番号规范化预检防缩位假冲突（比对 key = (系列字母, int(数字))）；批量导入 xlsx 必须走去重入口 `save_asin_to_excel`。
  - cid→番号规则：`^(\d*)([a-z]+)(\d+)([a-z]?)$` → `系列大写-{int:03d}`；tenhow cid 离线索引 `resources/userdata/tenhow_asin_cids.json`（36441 条）。
  - **外部归纳数据接入先做候选顺序回归**（覆盖率掩盖顺序污染）；防污染按"顺序影响"分级（append 兜底）而非二元弃用。DMM 路由表再生后必须先全量验证死链再推生产。
  - **DMM cid 结构**：前缀映射 + 数字双态（5 位补零 digital 与 3 位 mono 同系列可并存）+ 双路径；DMM 图床站点下架 CDN 不删对象，占位图 200+<4KB 已拒收。日亚图：SL1500 物理无条码、老商品标题半角片假名（NFKC 必做）、日亚 DVD 封与 DMM digital 封版本不同（比对天花板 ~0.62）。tenhow.net 图床 `images/{ASIN}.jpg` 与日亚 SL1500 同源免代理直取。
  - **ASIN 校验工程散点**：①数据治理前先 `Counter` 关键列识别导入批次残留；②openpyxl 迭代中 `delete_rows` 跳行，稳定模式=读出→去重→清空重写；③javbus 搜索不识别 ASIN，正查=番号→详情页标题比对；④**合并判定禁用 or 链**——`a.get(x) or b.get(x)` 短路吞判定值；#21 再现新形态：`if dict1.pop(k, None) is not None or dict2.pop(k, None) is not None` 短路使 dict2 的 pop 副作用不执行（冷却清零漏清），凡 or 链任一侧带副作用必须先求值再合并；⑤外部 API 错误码 marker 取响应原文字面值；⑥v2 裁决链覆盖 95%+，剩余人工行给用户一句话差什么证据。
