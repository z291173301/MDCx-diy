# 需求实施计划：超分工具分发（Windows/Linux 一体包 + 其余平台按需下载）

> 口径调整：不做任何镜像/加速配置（撤销 `poster_sr_mirror` 配置项与镜像解析逻辑及 UI 入口）。
> 分发形态：Windows、Linux 打包期内嵌工具；macOS 首次使用时从官方 release 按需下载。

- [x] 1. 修正 realesrgan Linux 资产名（证据：`https://api.github.com/repos/xinntao/Real-ESRGAN/releases/tags/v0.2.5.0`）
   - 现状：`_TOOL_DOWNLOAD_URLS` 对 linux 平台拼 `realesrgan-ncnn-vulkan-20220424-linux.zip`，实测 404，Linux 上功能必然降级
   - 官方资产为 `realesrgan-ncnn-vulkan-20220424-ubuntu.zip`（46.93 MB）；waifu2x 的 `...-20250915-linux.zip`（36.66 MB）正确，不要顺手改错
   - 采用「按 tool 覆盖平台资产名」的写法，避免 `_PLATFORM` 统一拼装掩盖这类差异
   - [x] 1.1 加回归测试：断言 realesrgan linux 源 URL 以 `-ubuntu.zip` 结尾、waifu2x linux 以 `-linux.zip` 结尾，防回归与误改

- [x] 2. 内置官方 zip 基准 sha256 与校验函数
   - [x] 2.1 新增 `_TOOL_CHECKSUMS`（tool × platform），基准值取自官方 release 资产（本环境已逐个下载核算，见常量注释）
   - [x] 2.2 新增 `_checksum_matches`：无基准时不拦截，有基准则比对，不匹配即按「该源失败」处理
   - [x] 2.3 写单元测试：基准一致放行、基准不一致判定失败、无基准放行

- [x] 3. 改造 `ensure_binary` 为「内置优先 → 按需下载」
   - [x] 3.1 查找内置工具目录：打包环境从 `sys._MEIPASS/sr_tools/<tool>`，源码环境从 `resources/sr/<tool>`；命中即整目录复制到 userdata 缓存后执行（二进制按自身目录解析模型与依赖库，单拷 exe 不可用）
   - [x] 3.2 未内置时按需下载官方源：连接 30s / 读 120s 超时收敛，临时失败重试 1 次，沿用现有 `manager.config.proxy`
   - [x] 3.3 先落 `.part` 临时文件再解压到临时目录，最后 `shutil.copytree` 到 `sr/tools/<tool>/`，避免半包被当成可用工具
   - [x] 3.4 下载后用 `_checksum_matches` 校验，不匹配则判失败降级
   - [x] 3.5 日志带出决策上下文：命中源（内置/下载）、失败原因分类（超时 / HTTP 状态 / 校验不匹配 / 解压失败）
   - [x] 3.6 全路径失败时给出可操作提示：手动解压到 `userdata/sr/tools/<tool>/<tool>[.exe]` 的完整路径
   - [x] 3.7 写单元测试：内置命中不触发下载、首源超时重试成功、首源 403/校验不符降级、全失败返回 None 且不改动原图

- [x] 4. 一体包：构建期拉取工具并内嵌（Windows + Linux）
   - [x] 4.1 新增 `scripts/fetch_sr_tools.py`：按当前平台拉取 realesrgan/waifu2x 官方 zip，校验 sha256 后解压到统一目录，供打包与 CI 复用
   - [x] 4.2 在 `scripts/build.py` 中按平台条件追加 `--add-binary`，把工具整目录（含 models 与依赖库）打进 Windows 与 Linux 包
   - [x] 4.3 打包参数回归：`tests/test_build_hidden_imports.py` 之外补一条断言，确认仅 Windows/Linux 包含工具资源、macOS 不包含
   - [x] 4.4 端到端对账：按解压后的真实布局实测一次启动（记忆：打包布局与启动入口假设必须端到端对账）

- [x] 5. 定期更新工作流与缓存复用
   - [x] 5.1 新增 `.github/workflows/update-sr-tools.yml`：`schedule`（每月）+ `workflow_dispatch` 手动触发，支持指定 tool/tag
   - [x] 5.2 校验门禁：下载后比对 `_TOOL_CHECKSUMS`，不一致即失败并保留旧缓存，避免坏包扩散
   - [x] 5.3 用 `actions/cache` 缓存工具目录，key 含 tool 与 release tag；release/package-trawl 工作流同一 key 读取，打包免下载提速
   - [x] 5.4 缓存未命中时自动回退直接下载，保证打包链路不因缓存回收而中断

- [x] 6. 许可证随附
   - [x] 6.1 收集并存放 Real-ESRGAN（BSD-3）与 waifu2x-ncnn-vulkan 的上游 LICENSE 到仓库（小文件，可入库）
   - [x] 6.2 在一体包构建时把 LICENSE 一并打入资源目录，并在 docs 说明第三方工具及其许可

- [x] 7. 检查点 - 确保所有测试通过
   - 确保所有测试通过,如有疑问请询问用户

- [x] 8. 文档与发布说明
   - [x] 8.1 跑 `uv run check --skip-hook-install`（含全量 pytest `-m "not network"`）
   - [x] 8.2 更新 `docs/`：说明 Windows/Linux 一体包内置版本来源与更新节奏、macOS 首次下载行为与手动放置路径
   - [x] 8.3 更新 `docs/changelog.md`：并入 v2.1.1 未发版段的 #26 条目

- [x] 9. 检查点 - 确保所有测试通过
   - 确保所有测试通过,如有疑问请询问用户

- [x] 10. 阈值默认值定为 800 并同步文案
   - [x] 10.1 `poster_sr_max_dim` 默认 1200 → 800（`mdcx/config/models.py`）
   - [x] 10.2 `.ui` tooltip 文案改为「内置/按需下载」两态并同步重编译 `MDCx.py`
   - [x] 10.3 `docs/CONFIGURATION.md` 阈值数值同步
   - [x] 10.4 超分不可用时打印一次性结论行（含 Vulkan 缺失判定）与回归测试
