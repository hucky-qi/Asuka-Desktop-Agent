# 本地文件与桌面助手（local-file-chat）

首次公开源码快照：2026-10-16。面向个人电脑的本地助手，包含三条功能线：

1. **文件助手**：自然语言找文件、高级筛选、内容问答、读取与确认式文件操作，关键词和本地embedding混合检索。
2. **桌面聊天**：macOS悬浮外壳、独立Qwen多轮会话、显式长期记忆、可选百度搜索摘要；可自行配置合法Live2D资源。
3. **本机日历**：真实系统时间、授权日历查询、预览确认式创建；已有文字修改实现，但提取稳定性和真实修改仍待验收。

这是个人项目的完整稳定源码导出，包含当前业务代码，不是最小聊天示例。它也不是已公证的通用桌面发行版。未包含私人运行数据或第三方SDK；追加了单独声明权利边界的Asuka v7最小同人运行素材。自有源码尚未指定开源许可证，公开可见不等于获得再分发授权，详见[许可边界](LICENSES.md)。

## 状态与平台

| 能力 | macOS | Windows |
| --- | --- | --- |
| 网页问答、文件检索、内容读取、语义索引 | 开发机已实测；复杂查询有已知限制 | 代码大部可复用，**未验证，当前不可承诺直接运行** |
| Python后端及联网计数 | macOS已实测 | `fcntl`文件锁等为Unix实现，需要适配 |
| Swift/AppKit/WKWebView桌面、原生快捷键 | 可构建；部分原生交互待验收 | 暂不支持 |
| EventKit时间/日历 | 读取和创建有用户人工成功记录；修改未完成真实验收 | 暂不支持 |
| Live2D显示接入 | 本机资源版本已测试，公开包不带模型/SDK | 桌面外壳尚未适配 |
| DeepSeek、主动提醒、手机闹钟 | DeepSeek仅保留禁用适配代码；其余是规划 | 同左 |

Windows不是“只能聊天”：搜索和索引实现随源码提供，但本轮未做Windows兼容测试；目前直接导入Unix锁模块也会阻断启动。不要把下面macOS命令照搬到Windows当作验证过的安装流程。

## 环境与安装（macOS）

建议Python 3.11或更新版本（开发机Python 3.13），Ollama本地服务，以及安装依赖的网络。桌面另需Xcode Command Line Tools和macOS 13+；日历完全访问API按系统版本走授权流程。Live2D构建另需Node/npm、用户合法取得的Cubism Web SDK与模型。Web模式不需要Node或Swift。

```sh
git clone https://github.com/hucky-qi/-Q-.git
cd -- -Q-
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

自行安装并启动[Ollama](https://ollama.com/)。以下**显式命令会下载较大模型**，安装脚本不会替你执行：

```sh
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:0.6b
```

聊天与embedding是两种不同模型。名称分别配置在[settings.py](settings.py)的`MODEL`和`EMBED_MODEL`。文件助手使用默认Ollama client，桌面聊天当前显式连接`127.0.0.1:11434`；不要假设改一个环境变量就能让全部入口连接远端。自定义服务需要审查两处client配置，发布版没有自动模型路由。

### 网页模式

```sh
.venv/bin/python app.py
```

浏览器打开 http://127.0.0.1:8765/ 。前台终端按Ctrl+C停止该后端。`/bubble`是桌面小聊天对应页面；完整网页默认“文件助手”，不是普通联网闲聊入口。不要把服务绑定到公网，此项目没有完整远程多用户认证隔离。

也可运行`.venv/bin/python start.py --web-only`按需启动/复用后台服务，日志在`.runtime/server.log`。它不会停其他服务；停止后台服务须先确认是本项目进程再终止，不使用全局kill命令。

### 同机并行安装与隔离端口

默认后端8765、Live2D资源8766。已有助手或其他服务占用时，不结束它们，启动前在当前终端明确配置空闲端口：

```sh
export LOCAL_FILE_CHAT_PORT=18765
export LOCAL_FILE_CHAT_LIVE2D_PORT=18766
.venv/bin/python app.py
# 或在同样的环境中运行：.venv/bin/python launch_desktop.py
```

端口必须为1–65535，仍只绑定127.0.0.1。后端、人物及资源服务沿用同一配置，资源health必须属于当前项目，不能复用别的克隆。变量由`settings.py`读取，不从百度`.env`加载；未设置时原默认不变。若希望**双击.app**也使用非默认端口，可修改`settings.py`中`PORT`/`LIVE2D_PORT`的默认值；不要假设Finder启动会继承终端export。临时验收应用可用`macos/create_desktop_app.py --output /path/to/InstallationAcceptance.app`生成到独立目录，不覆盖桌面现有应用。

验收/演示仅用人工样本时，可按已有配置方式将`settings.SEARCH_ROOTS`限制为`{"workspace": WORKSPACE}`，不要点击全盘/私人目录索引。此项是验收范围配置，不是更改发布默认搜索范围；启动不自动开启索引。

### macOS桌面模式（无人物素材也能用）

```sh
.venv/bin/python launch_desktop.py
```

未构建Live2D时使用已有蓝色原生回退角色，提供小聊天及完整文件助手入口。Asuka v7运行素材已附带，SDK仍需官方获取并本机构建，详见下方。单击切换聊天，拖动移动，右键打开文件助手、停止等待任务或退出。退出人物不会停止后端/Ollama。确认式日历功能需要下面的正式本机应用入口，直接运行Python人物不等于有EventKit授权身份。

生成本机入口（不附带角色图标，默认系统图标）：

```sh
.venv/bin/python macos/create_desktop_app.py
```

会在桌面生成“明日香助手.app”，源码/数据仍在克隆目录；不要移动项目后继续用旧入口。可用`--icon /path/to/your-authorized-image.png`提供自己有权使用的图片，原比例转换；不会自动获取角色图。首次运行可能需要系统文件夹和日历访问授权。应用是本机ad-hoc签名，不是公证安装包；命令生成的入口与当前私有部署是同一启动逻辑，但本轮未替换用户现有桌面应用。模型与第三方许可见[DESKTOP](docs/DESKTOP.md)。


## 可选Asuka v7同人运行资源（本次补齐）

已附带model3.json实际引用的5个文件：moc3、单张1024纹理、DisplayInfo、头发physics3及model3本身；不含旧版/编辑源/录屏/SDK。眨眼、呼吸、歪头、头发输入仍由现有代码驱动，没有缺失的motion3文件要另行下载。制作过程及AI来源核实限制见[素材NOTICE](live2d/models/Asuka/NOTICE.md)，资源哈希见[manifest](live2d/models/Asuka/manifest.json)。**非官方同人、未声称官方授权；同人指南未明确涵盖GitHub模型包，商业/再分发及原图权利仍需确认。**

从[Live2D官方](https://www.live2d.com/en/sdk/download/web/)自行取得Web SDK并核对条款后：

```sh
cd live2d
npm ci
../.venv/bin/python prepare.py --sdk /path/to/CubismSdkForWeb-5-r.5 --asuka
npm run build
cd ..
.venv/bin/python launch_desktop.py
```

SDK不进入Git，代码/角色素材/SDK分别适用不同权利边界，详见[SDK获取与条件](live2d/SDK_NOTICES.md)和[LICENSES](LICENSES.md)。不构建时保留原蓝色回退；本轮不提供打包App或SDK镜像。

## 搜索目录与索引

侧栏“索引与目录”选择范围，点**添加目录**，选择基础目录再输入已存在的子目录，例如`desktop/Research`、`documents/Projects`。范围支持`workspace`、`Desktop`、`Documents`、`Downloads`及这些目录内的非隐藏子目录。可用完整Mac路径，例如`/Users/你的用户名/Documents/Projects`，不允许`..`逃逸。新用户`workspace`位于项目内；若项目相邻已有workspace，则复用相邻目录，见settings.py。

自定义范围与保存搜索由`.assistant_preferences.json`管理：`scopes`条目包含`name`和`path`，`searches`保存条件，`auto_index`为布尔值。推荐通过界面修改，**不用复制开发者的配置文件**。若需不同基础根目录，审查`settings.SEARCH_ROOTS`。Windows通用路径示例`C:\Users\YourName\Documents\Projects`仅说明路径形式，当前别名和锁实现未经Windows验证。

检索会递归子目录，忽略`.git`、虚拟环境、node_modules、Library、缓存等`settings.IGNORE_DIRS`中的目录；仅在允许根目录和操作系统权限内读取。文件扫描/正文解析有安全上限，遇到partial应查看覆盖说明，不把前几项当作整机全部结果。图片/视频主题只依据名称、目录和已有元数据，**没有视觉内容索引**。

首次语义使用需要在目标范围点**更新语义索引**，或明确执行：

```sh
.venv/bin/python build_index.py --scope workspace
```

该命令会读取范围内文件、调用本机embedding并保存正文分块及向量，不会下载模型。网页可取消索引更新；再次更新依据mtime/大小/模型复用未变文件。自动每15分钟更新默认关闭，仅用户开启后生效。普通检索不隐式全量重建；未建索引时名称/目录及受控内容搜索仍可用，概念检索覆盖不足须如实报告。普通搜索目标5秒、复杂约15秒不是保证，语义复核在模型竞争时可能明显超时并返回partial。

“文件助手”与“高级筛选”共享当前结果和文件身份；可追问“只看PDF”“按最近修改排序”“打开第二个”“第二问要求什么”。文件写入/移动等操作核对界面确认，不把聊天回答当作操作成功。详见[SEARCH](docs/SEARCH.md)。

## 可选联网与模型接口

联网默认未配置、复选框默认关闭。复制`.env.example`为**项目目录下**`.env`，只在本机填自己的百度API Key；不提交该文件。启动会安全解析允许的百度变量，不执行shell；已有进程环境变量优先。Key由后端读取，不能发给前端。

需要人工核对开通及计费后才配置`BUBBLE_WEB_ENABLED=true`、`BAIDU_FREE_RESOURCES_CONFIRMED=true`。勾选后本次发送至多一次官方独立web_search，百度返回标题/摘要/URL，本地Qwen归纳，来源标“百度搜索摘要”；不声称读取网页全文。无结果/失败不伪造回答，不自动重试。未勾选不搜索，启动/刷新不探测百度。

**本助手近24小时最多30次**：失败请求计数，发送前拦截和缓存不计数；文件锁持久化并发预占。日常用户联网和独立开发预算不同，日常入口仍受30次共同上限，开发测试另受累计锁；不能借日常入口做超预算开发测试。新安装不包含作者历史账本，示例开发上限2，离线测试真实预算0。作者私有部署的7/7历史锁保留在原机，不复制进公开包。**本地计数不是官方剩余额度或绝不收费保证**，同账号其他程序不在本地账本里。百度鉴权在开发机仍有历史失败，不能宣传全面接入成功。

DeepSeek适配接口已保留，默认禁用、未真实验证、无自动降级；本轮不启用。详情：[MODELS_AND_WEB](docs/MODELS_AND_WEB.md)。

## 日历与隐私

通过桌面.app正常申请EventKit访问；拒绝、撤销、读取失败和无事件分别处理。可以问“今天有什么安排”，或“帮我把明天下午3点到3点半的资料整理加入日历”。代码使用系统时区，周一为周开始，支持今天/明后天、完整日期、本/下/下下周加星期、本/下月明确日期；模糊日期、上午下午、无效/DST歧义会澄清。新建缺结束时间默认30分钟，预览标注“默认30分钟，可修改”，明确时长优先。完整预览并明确确认后才写入和读回；不把记忆当成已创建事件。

已有修改实现带身份、冲突、确认、幂等及读回，但自然语言提取存在失败，真实修改/原生确认按钮**尚未验收**；未通过候选未发布。只改开始时间时保留原时长，不能用创建默认值覆盖。当前无删除、重复/批量修改、主动提醒或手机闹钟。不承诺到点提醒。日历详情：[CALENDAR](docs/CALENDAR.md)。

本地Qwen可收到本次问题、有限会话历史、显式记忆及工具所需文件证据；本机embedding收到文件分块/查询。联网仅发送必要查询词，不应把私人路径、文件内容、日历或长期记忆上传；用户仍应避免在联网问题中输入隐私。页面和工具不是对抗恶意本机用户的安全沙箱。数据目录、升级策略和第三方边界：[数据说明](docs/MODELS_AND_WEB.md)。

升级时保留本机`.semantic_index.sqlite3`及附属文件、`.assistant_preferences.json`、`.bubble-memory.json`、`.bubble-web-usage.json`、`.calendar-create/`、`.calendar-update/`；这些都不在仓库。先停止本项目写入再做一致性备份，不清空索引、不复制别人的私人数据库。不要用`git clean -xfd`删除被忽略的数据。

## 测试与已知限制

安装离线测试依赖后执行一条命令（本次验证的完整依赖快照另见`requirements-lock.txt`，可用于复现环境）：

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run_tests.py
```

默认仅隔离HTTP模拟依赖、人工文件和独立会话，失败即停止，拒绝真实Ollama/百度/私人日历连接，不读取正式索引。`--real`被拒绝。原生鼠标、系统中文输入法和快捷键需要独立人工验收；浏览器合成事件不是原生通过。真实模型测试须另建显式policy并通过`acceptance/test-safety/safety.py`中的RealGate在发出前预占查询/调用双预算，不在快速命令中启用；不会自动下载模型或消耗付费API。

开发机已有：中文输入、拖动、时间/日历读取的人工成功记录；真实创建有成功记录。复杂语义并发曾约37.4秒并有复核超时/partial0结果，embedding实际参与，不能说关闭语义后达到1秒。部分“成绩”主题排序和约19.73秒首次耗时仍有问题。日历修改模型漏字段、Mac快捷键真实按键、最新回答自动弹窗的原生视觉/焦点仍待验收。

历史有限内存观测（macOS24GiB、Qwen9B、约2秒采样、其他agent同时运行）：原生人物待机RSS中位约101MiB、加载后约64MiB；处理请求的隔离Python后端中位约33MiB；Qwen runner处理期中位约6.08GiB，观测峰值约6.39GiB。不是绝对最大需求，不相加共享内存，WebKit/GPU辅助进程未完整归属；系统swap较高不能归因本项目。本轮发布检查不调用模型、不重跑内存测试。详见[测试与验证范围](docs/TESTING.md)。

## 文档导航

[文件搜索](docs/SEARCH.md) · [桌面](docs/DESKTOP.md) · [日历](docs/CALENDAR.md) · [模型/联网/数据](docs/MODELS_AND_WEB.md) · [测试](docs/TESTING.md) · [路线图](docs/ROADMAP.md) · [许可](LICENSES.md)

后续：先收敛创建/修改原生验收和复杂检索遗留问题；主动提醒、iPhone快捷指令原型、可编辑语音输入、完整App/模型管理、Windows适配、更多动作、DeepSeek显式付费确认、VPN及网页操作均是规划，未实现的不写成支持。

## 全新安装验收（2026-10-16）

从远端`9aff2c2`在项目之外全新clone，独立Python venv、pip无缓存安装、独立npm依赖/缓存，无开发目录环境、构建物或私人数据。macOS本次结果：

| 项目 | 实际结果 |
| --- | --- |
| README运行/测试依赖安装 | 通过 |
| 无Key后端、网页、闲聊页面 | 通过；联网关闭/待配置，不伪造成功 |
| 人工名称搜索/PDF筛选/空结果 | 通过；未建语义库，明确仅名称覆盖 |
| 无SDK且临时移走模型后的蓝色回退 | 独立构建的测试App实际启动并就绪 |
| 官方SDK全新下载 | **未完成**：需用户阅读/同意条款，首次下载有身份/邮件输入，本轮未代接受或下载 |
| 已有合法本地Web 5-r.5 SDK续测 | 明确使用用户Downloads内SDK，准备/build/实际浏览器渲染/原生WKWebView就绪通过；不是全新下载通过 |
| .app构建及启动 | 独立目录构建并启动，通过blue及Asuka原生就绪；未覆盖桌面入口、未请求日历权限 |
| 原生点击聊天/右键打开完整网页 | 未自动验收；对应真实浏览器页面及导航通过，不冒充原生鼠标通过 |
| 模型问答/语义及云端/私人日历 | 真实调用0，不在本轮功能验收范围 |

必要修补仅支持隔离端口、验证资源服务所属项目及说明启动方式；默认行为不变。新用户仍需自备Python/Ollama、需要时自行下载模型，Live2D需自行从官方完成SDK许可/下载；本轮没有下载大模型。Windows仍未实测。详见[TESTING](docs/TESTING.md)。
