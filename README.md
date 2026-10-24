# 本地文件与桌面助手（local-file-chat）

macOS稳定预览版源码快照：2026-10-24。复用原项目，三条核心能力是聊天与记忆、文件助手、macOS日历与助手提醒。DeepSeek用于默认理解/对话；Qwen为显式本地选项及既有本地正文复核，embedding在本机负责语义检索。程序管理可信文件/事件身份、条件、权限、取消和确认；模型回复不代表操作成功。

## 支持与限制

| 能力 | macOS | Windows |
| --- | --- | --- |
| Python网页、文件查找/过滤/读取/索引 | 已有隔离回归；当前干净安装结果见TESTING | 未实测；fcntl等Unix锁目前会阻断，不承诺直接运行 |
| DeepSeek聊天/理解、正文授权、记忆 | 代码已接入；无Key时启动但不能产生云端答案 | 后端可迁移，未验证 |
| Swift/AppKit桌面、EventKit日历 | 使用兼容的Xcode SDK26构建；编译与无SDK启动就绪协议已验收，原生点击仍待验收 | 不支持 |
| 中文主动录音转文字 | macOS26+ Apple SpeechTranscriber，需中文资产和用户麦克风权限 | 此方案不支持 |
| Live2D显示 | 可选SDK＋模型本机构建，缺失时蓝色助手回退 | 本桌面壳不支持 |

已实现：多轮文件筛选/排序、可信选择及正文问答；明确偏好、日常记录与候选积累；日历查询和单次创建/修改/有限删除的预览确认；显式事件提醒、共用气泡及稍后提醒；主动录音只回填草稿。推测偏好自动生效默认关闭，正式提醒默认关闭。复杂/重复/与会者事件不保证可改删，独立于日历的提醒、持续监听/朗读、Codex委派未实现。日历卡片曾有一次用户真实修改通过，不代表全场景通过。

## 安装（macOS）

需要Python3.11+（验收用3.13）、兼容SDK26的Xcode Command Line Tools；纯网页不需要Swift或Node。不要复制开发机的虚拟环境、数据库或父目录.env。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python start.py --web-only
```

打开终端显示的地址（默认 http://127.0.0.1:8765）。资源端口默认8766，可通过真实配置项 `LOCAL_FILE_CHAT_PORT` 与 `LOCAL_FILE_CHAT_LIVE2D_PORT` 选择空闲端口；端口占用时停止启动，不杀其他进程。仅绑定本机回环地址。启动不自动索引、不申请日历权限、不下载模型。

`.env`在**本项目目录**，已有进程环境变量优先。无Key可查看网页和配置、管理本地文件；需要模型的请求会说明缺配置，不伪造回答。DeepSeek启用：自行填写 `DEEPSEEK_API_KEY`，将 `DEEPSEEK_ENABLED=true`，文件理解设 `DEEPSEEK_UNDERSTANDING_ENABLED=true`，日历理解需要 `DEEPSEEK_CALENDAR_ENABLED=true`。只接受官方HTTPS地址和显式provider，不探测余额、自动重试或静默换模型。

日常直接聊天需用户自行启用已有本地策略（不产生API请求）：

```sh
.venv/bin/python - <<'PYCONFIG'
from pathlib import Path
import json
p=Path('.runtime/daily-chat-policy.json');p.parent.mkdir(exist_ok=True)
d=json.loads(p.read_text()) if p.exists() else {}
d.update(enabled=True,preference_learning_auto_activate=False)
p.write_text(json.dumps(d))
PYCONFIG
```

开启后，普通问题和必要当前会话上下文发给DeepSeek，不逐条确认；高级数据控制、正文、日常记录云端许可仍独立。未获准旧记忆不外发，设置不应批量改条目许可。没有日/月消费默认承诺；单次16000字节保护与输出预算、实际usage和用户显式保护保留。开发预算与日常配置隔离，关闭开发许可不关闭已启用日常配置。费用估算不是供应商账单。

## 本地模型与语义检索

用户自行安装[Ollama](https://ollama.com/)，显式下载所需模型（下列命令会下载大文件，本项目不自动执行）：

```sh
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:0.6b
```

`backend/settings.py`的`MODEL`与`EMBED_MODEL`分别控制聊天/复核模型和向量模型。通过气泡或网页的设置选择本地Qwen，云端失败不会替你切换。既有本地正文复核可能需要Qwen；不把它宣称已全部迁到DeepSeek。未安装模型时仍可启动并查看配置，不能宣称检索语义质量已保证。

## 搜索目录、索引与文件问答

网页“索引与目录”→“添加目录”，选择已允许的基础目录与已存在子目录；例如 `documents/Projects`、`desktop/Research`。持久化在 `.assistant_preferences.json` 的 `scopes`；内置根目录由 `settings.SEARCH_ROOTS` 配置。Mac路径示例 `/Users/your-name/Documents/Projects`；Windows `C:\Users\your-name\Documents\Projects`仅说明格式，不能当作已验证配置。默认workspace位于项目内，不探测父目录workspace。

子目录受根范围、隐藏目录、IGNORE_DIRS和系统权限限制；添加目录不等于全盘授权。不要将用户目录或整盘作为验收样本。首次概念检索需在所选目录点击“更新语义索引”，或显式运行 `.venv/bin/python build_index.py --scope workspace`。该操作读取目标正文并调用本机embedding；未改文件增量复用，自动更新默认关闭，可取消。没有索引时名称/目录与受控内容查找仍可用，但语义覆盖不足必须说明。复杂检索可能慢或部分覆盖；没有用降低阈值美化结果。

名称条件、正文关键词和语义概念使用不同工具契约；本轮修复名称条件被重复正则改成正文的问题，保留原排序和硬过滤。任意独立名称词＋正文词复合搜索不作新增支持承诺。先显示卡片，选择绑定后端快照；未读取正文不能宣称理解整份文件。正文云端授权针对具体文件/版本/范围，符合范围的追问复用；换文件、改版本或扩大范围重新核验。大文件只发送相关片段，覆盖不全如实说明，扫描PDF/加密/损坏/不支持格式有失败提示，不编造页码。

## macOS桌面与可选模型

先完成Python安装。无SDK/模型时可直接启动蓝色回退：

```sh
.venv/bin/python launch_desktop.py
```

创建自己的应用入口（不要覆盖另一份正在运行的开发实例）：

```sh
.venv/bin/python macos/create_desktop_app.py --output "$PWD/.runtime/明日香小助手.app"
```

可选 `--icon /path/to/your-licensed-icon.png`，未提供时无角色图标依赖。应用固定项目路径及应用身份，移动项目后应重新构建；非公证通用安装包。原生退出只退出助手，不结束后端或Ollama；停止后端应在确认项目路径和PID后结束**本项目**进程，不能按端口批量杀进程。窗口退出不保证后台提醒仍能展示。

Live2D无需语音或模型API。按[SDK说明](live2d/SDK_NOTICES.md)从官方取得并由你接受适用条款，再执行：

```sh
.venv/bin/python live2d/prepare.py --sdk /path/to/CubismSdkForWeb-5-r.5 --asuka
cd live2d
npm ci
npm run build
cd ..
.venv/bin/python launch_desktop.py
```

自有合法模型可将 `--asuka`换为`--model /path/to/model.model3.json`。SDK/Core/Framework/shader不随仓库分发；不静默借用Downloads或隐藏SDK。现有Asuka最小运行素材仅沿用先前公开字节及NOTICE；非官方同人，权利适用范围仍未确认，不声称官方授权。源工程、参考图、图标、模型编辑备份不在包内。[代码、素材与SDK许可分别说明](LICENSES.md)。

## 中文语音准备

需macOS26+、SDK26和Apple支持的硬件/语言。可明确检查或安装系统中文资产：

```sh
.venv/bin/python tools/speech_assets.py --status
.venv/bin/python tools/speech_assets.py --install
```

`--status`只读状态、不下载、不请求麦克风；`--install`经Apple AssetInventory安装到OS管理缓存，可联网下载，大小/版本由系统提供。已有开发机观测约578MB、版本3100.44005.81607只是历史记录，非新安装传输量保证。新模块的supported状态不能单独证明缺资产，命令同时列出installed_locale。缓存由系统复用，不从旧电脑复制、不放仓库、不在每次启动重新下载。系统资产属于Apple专有资源，未取得再分发授权，不按项目代码许可分发。安装与系统缓存机制参考[Apple官方SpeechAnalyzer说明](https://developer.apple.com/videos/play/wwdc2025/277/)。

启动本项目原生应用后，页面检测本机桥接与资产；不可用时隐藏麦克风，文字输入仍可用。点击录音、再次点击停止，结果追加可编辑草稿，用户手动发送；不自动创建事件。不支持浏览器云语音回退，不静默上传录音。默认录音不保存，临时文件清理；系统麦克风权限由用户处理。直接启动网页/仅蓝色人物脚本不等于主应用语音桥接已启动。本轮干净副本验证工具编译和只读能力状态；原机缓存命中与全新下载、真实麦克风验收分开记录。

## 日历、提醒与记忆

日历依赖macOS原应用的EventKit桥接和用户系统许可。取消/拒绝后使用“继续授权/重新检查”或系统设置指引，不能保证拒绝后一定再弹窗；字段保留，权限恢复不自动写入。创建/修改/删除均先具体预览并明确确认、绑定对象/版本并读回；创建缺结束时默认30分钟且标注，修改保留原时长。日期以系统时区计算，周一为周首；缺上午/下午、无效或过去时间具体澄清。复杂重复/共享/含与会者事件不扩大处理。

助手提醒只面向用户明确选定的可信普通单次未来事件，不默认监控所有日历，不写EventKit alarm。正式开关默认关闭，用户自行验收启用；关闭处理待投递计划，不只是隐藏。默认提前30分钟清楚显示；稍后/自定义需持久化成功才反馈。睡眠/关机/退出无法保证准时；恢复逾期不超过10分钟且事件未开始才补发一次，未知投递不盲目重发。与系统日历提醒可能同时存在，不能认为创建日历即提醒成功。

长期记忆沿用 `.bubble-memory.json`，明确风格偏好与日常记录分类；普通重置不删除记忆。候选不等于已生效，自动学习生效默认关闭；遗忘控制后续召回，不宣称历史聊天/备份已同时彻底删除。正文、搜索摘要和模型回复不会自动变成用户事实；记忆不授予工具权限。日常记录云端许可与风格许可独立。

## 可选聊天保存与定期整理

记忆设置仍是“已保存记忆”“定期整理日常记录”“重复表达学习”三个分区。普通聊天保存**默认关闭**；在“定期整理日常记录→聊天来源设置与管理”按当前入口选择开启。只保存开启后的普通聊天，不追溯未保存历史；不把工具结果、文件正文、日历对象、授权、草稿或未提交输入混入来源。桌面与完整网页的保存/许可分别管理，默认保留30天，可设1～90天并独立删除。

**本地保存、来源交给DeepSeek提炼、采纳后正式记忆的云端召回是三件独立的事。** 开启保存不外发；云端整理需另外允许该入口和来源。旧条目未许可不发送。提炼计划默认关闭，每7天一次，可调整/暂停，运行时只处理新增或变化来源，最多一批3段/条，并提供有界继续入口；助手或后端停机时不运行，错过多周期只补一次，不承诺关机后台运行。

结果只进入候选：查看来源、日期与角色，必要时编辑指代/对象，再手动采纳或拒绝；冲突先选择，不任选覆盖。重复整理或重复采纳不生成副本。采纳默认不新增云端召回许可，逐条拒绝、撤销和遗忘继续优先。**自动采纳未开放**，重复表达学习也仅候选模式。

停止保存不删除旧来源；重置对话不删除来源或正式记忆。来源过期/删除后不再整理，未采纳候选失效，例行清理不会自动删除已采纳记忆。删除来源时可查看关联记忆，并选择是否遗忘仅由该来源支持的条目；独立依据不扩大删除。遗忘影响后续召回，不承诺一并清除历史聊天、外部备份。长期运行、跨睡眠及真实原生组合仍在观察。

## 百度与数据位置

联网默认关闭、无Key提示不可用；用户勾选后一次操作一次搜索，无自动补搜/重试。摘要归纳provider显式选择，来源只能来自搜索响应；未抓取网页全文，不说已通读。24小时滚动本地30次计数不等于供应商免费保证，失败也计尝试。开发测试锁和最终出站预算独立，不能借日常路径绕过测试上限。自行核对供应商计费/免费资源，项目不自动开通服务。

私有数据在项目：`.semantic_index.sqlite3`及附属文件、`.assistant_preferences.json`、`.bubble-memory.json`及防降级/恢复文件、`.bubble-web-usage.json`、`.deepseek-usage.json`、`.runtime/`与日历操作日志。不得提交这些文件；升级先停止本项目写入并一致备份，再保留全部配套文件，不用git clean清空。会话恢复与长期记忆分开，不持久化可重放操作确认或录音正文。Key只后端读取，不写网页或日志。

## 验证与已知限制

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run_tests.py
```

默认离线，mock依赖、独立数据及最终出站保护；缺mock失败，不访问真实模型/百度/私人日历。当前安装与各测试结果见[TESTING](docs/TESTING.md)，人工验收见[MANUAL_ACCEPTANCE](docs/MANUAL_ACCEPTANCE.md)。真实测试另需明确次数/费用/数据范围，在出站前预占；不在快速测试自动付费。

当前发布通过隔离黑盒、完整合成聊天来源旅程及无Key启动检查，具体范围见TESTING；不声明普遍偏好遵循、一般知识正确率或所有自然表达准确率。单次16000字节保护保持不变，较长历史仍可能触及保护，此时保留任务并说明，不自动重试或切模型。严格区分服务传输不完整、JSON语法、Schema、来源及权限错误，不从坏响应拼补事实。

原生中文输入法、焦点、隐藏恢复与提醒打断组合仍需集中人工验收；语音长音频、真实睡眠恢复和长期定期整理继续观察。没有把短时/模拟测试当作连续七天或关机期间可靠运行的证据。Windows尚未适配。没有启用自动采纳、自动回复、微信读取或其他模型委派。


## 源码目录与开发命令

[目录说明](docs/README.md#directory-layout)统一维护结构和数据路径。启动仍为 `.venv/bin/python start.py --web-only` 或 `.venv/bin/python launch_desktop.py`；从其他目录可传上述脚本的绝对路径。`app.py`、`desktop_pet.py`仅保留必要命令兼容，业务源码已归包。默认离线测试仍为 `.venv/bin/python run_tests.py`；Live2D状态测试为 `node --test tests/live2d/state-controller.test.mjs`。测试运行输出不进入发布清单。
