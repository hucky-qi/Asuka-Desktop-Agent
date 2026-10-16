# macOS桌面

[导航](README.md) · [启动命令](../README.md)

现有Swift/AppKit/WKWebView外壳、输入组合保护、拖动/点击区分、气泡定位、任务ID/版本防迟到、单实例和原生菜单随源码提供。发布入口`launch_desktop.py`启动后端并按资源存在与否选择可选Live2D或原有蓝色回退。生成桌面.app见`macos/create_desktop_app.py`，构建需要本项目`.venv`和swiftc；无角色图标时用系统默认图标。单实例按项目锁实现，不承诺不同克隆之间共享单实例。

发送后隐藏回答框，等待时只显示状态；本地处理三点、外部搜索Wi-Fi，结束先关闭等待再一次显示完整答案/预览。只有有效本地请求可以触发自动弹窗，不主动激活键盘焦点；网页任务不写入桌面答案。停止在右键菜单可用。最新重载页面ID修补已进入发布源码；协议和隔离浏览器已通过，原生自动弹窗/焦点视觉仍待人工确认。Mac快捷键标准菜单已实现但真实按键未验收；系统中文输入、拖动边界有既往用户人工通过记录，不能算自动化覆盖。

## 可选合法Live2D资源

已追加Asuka v7最小运行资源，见[素材NOTICE](../live2d/models/Asuka/NOTICE.md)及[manifest](../live2d/models/Asuka/manifest.json)。不包含Core/Framework/shader/官方示例、编辑源cmo3/PSD/KRA、参考图、应用图标或打包资源。AI辅助同人和自己拆层绑定不代表取得角色官方授权；适用范围未确认事项均单独列出。

自行从[Live2D官方](https://www.live2d.com/en/sdk/download/web/)取得适合本机的Cubism SDK for Web，阅读其各组件条款，另备合法model3.json和所引用资源：

```sh
cd live2d
npm ci
../.venv/bin/python prepare.py --sdk /path/to/CubismSdkForWeb --model /path/to/authorized/model.model3.json
npm run build
cd ..
.venv/bin/python launch_desktop.py
```

prepare仅复制本机指定资源，包含路径越界检查；不自动下载SDK或示例。随库同人v7可将`--model ...`替换为`--asuka`，保留实际比例与物理配置；此构建路径沿用Web R5目录契约，其他SDK版本兼容性未验证；参数动作绑定依具体模型，不能保证任意模型都有相同动作。`.sdk/.assets/.runtime`均私有忽略，无需素材即可用蓝色回退。官方许可、商业使用/发布义务由使用者按实际SDK和模型核对，本项目不重新授予第三方许可。

2026-10-16：发布副本将固定相邻虚拟环境路径改为项目内`.venv`，提供无素材入口；不改变原机应用身份/模型或正在运行的助手。未发布原生应用二进制。

发布副本另增加无模型原生就绪标记，避免.app把正常蓝色回退误判为启动超时；应用权限说明与当前确认式创建/修改能力一致。未触碰原机应用或签名。

本次发布增量只补齐运行资源/构建与权利说明，未改角色动作、搜索或日历。SDK不分发，完整步骤与条款依据见[SDK_NOTICES](../live2d/SDK_NOTICES.md)。

2026-10-16全新安装修补：`settings.PORT/LIVE2D_PORT`支持`LOCAL_FILE_CHAT_PORT`/`LOCAL_FILE_CHAT_LIVE2D_PORT`；CLI父子进程一致，Live2D health须属于当前克隆。默认不变、占用不杀进程，不从百度.env读取端口。Finder启动不保证继承shell export，可改settings默认值；测试App输出到独立目录且没有替换原入口。blue/Asuka真实原生就绪通过，点击聊天/右键导航仍是原生缺口，浏览器页面通过另记。
