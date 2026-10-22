# 文档入口

[项目安装和当前能力](../README.md) · [模型与联网](MODELS_AND_WEB.md) · [搜索](SEARCH.md) · [日历](CALENDAR.md) · [桌面与语音](DESKTOP.md) · [测试](TESTING.md) · [人工验收](MANUAL_ACCEPTANCE.md) · [排期](ROADMAP.md)


<a id="directory-layout"></a>
## 目录与路径约定

| 位置 | 职责 |
| --- | --- |
| 根目录 | README、配置/依赖、project_paths.py和稳定命令app/start/launch_desktop/desktop_pet/build_index/run_tests |
| backend/assistant | 会话、上下文、任务及各工具的统一入口 |
| backend/files | 原有搜索、硬过滤、语义索引、读取及复核 |
| backend/memory | 统一记忆、明确偏好、日常记录和许可过滤 |
| backend/calendar | 原有日历身份/预览/确认、提醒及测试适配 |
| backend/providers | 模型、云端授权/预算、百度及用量 |
| backend根 | HTTP服务、设置、诊断、人格及原生上下文接口 |
| frontend/ | 双入口HTML和浏览器脚本；公开HTTP地址仍为/bubble、/speech-view.js等 |
| macos/ | Swift源文件、Python原生启动/语音桥接与构建帮助 |
| tests/ | unit、http、safety、live2d；默认run_tests.py选择隔离离线组 |
| docs/、tools/、live2d/ | 现有文档、安装/发布工具和模型接入；第三方许可边界不变 |

project_paths.py集中定义PROJECT、FRONTEND、MACOS和SWIFT_CACHE。所有数据、账本、锁、二进制与缓存仍落在原项目根目录及.runtime，不从cwd或业务模块所在目录推导。源码使用显式包导入，不添加逐模块兼容壳或sys.modules别名。保留原6条命令，支持从其他工作目录按绝对路径调用；业务模块不再作为根目录脚本导入。旧acceptance证据不迁移、不删除；使用新测试入口运行当前源码。

维护时先盘点静态/动态导入、源文件加载、HTTP资源、原生构建、启动器、测试快照和发布清单。tools/release_manifest.json是公开导出allowlist；tools/export_release_candidate.py从现有公开Git基线导出，不创建新历史、不自动提交/推送。公开文档、许可和既有模型来自已审查--base，业务源码来自--source；不把开发文档中的私人验收链接一起导出。只在新输出目录清除清单外的旧跟踪源码，不修改开发目录。
