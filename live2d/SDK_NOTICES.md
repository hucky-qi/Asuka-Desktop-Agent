# SDK获取、组件边界与未确认事项

核对日期：2026-10-16。此次Git增量**不分发SDK/Core/Framework、shader、官方示例或构建bundle**。接入代码仍由用户本机SDK构建；不是另建SDK镜像，也没有第三方自动下载。

## 逐项区分

| 文件/组件 | 来源与条款 | 本次处理 |
| --- | --- | --- |
| main.ts、state-controller.mjs、waiting-bubble.mjs、build/prepare/server | 本项目接入代码 | 沿用已有发布，不能作为第三方许可证 |
| Core/live2dcubismcore.min.js | 本机Web 5-r.5 SDK；文件头标识Live2D版权及Redistributable Code；适用[Proprietary Software协议](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html) | 不上传；由用户官方获取 |
| Framework/src、Framework/Shaders/WebGL | 本机SDK；Framework LICENSE指向[Open Software协议](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html) | 不上传；本机准备时保留代码版权头及随包LICENSE/NOTICE |
| SDK示例/Wanko | 另有Free Material及模型专属条款 | 不复制、不作为公开回退依赖 |
| 编辑工程、参考图、图标、旧运行版本、录屏 | 非最小运行依赖，含角色/私人开发资源 | 不上传 |
| Asuka v7五个运行文件 | 本项目AI辅助同人图、拆层/绑定及运行物理 | 独立[素材说明](models/Asuka/NOTICE.md)，不套用SDK或代码许可证 |

## 条款依据与暂停范围

Core协议§6.2的限制原文片段是“may not distribute or disclose all or part of this Software”，同时明确以协议其他许可为例外。§5允许符合条件的Redistributable Code随应用分发，附有用途、下游保护、原样分发等条件；因此**不能只据§6.2宣称Core绝对不可分发，也不能只据文件头就无条件上传**。本轮没有确认整个应用是否满足所有相关条件，暂停SDK/Core打包，提供官方获取方式。

Core协议§6.8还限制复制手册等资料；不把SDK手册或官网全文复制到仓库，仅引用必要条款和官方链接。Framework有条件的分发规则见Open Software协议§2.2，不能把Framework误标成MIT，也不为整个SDK新授许可。

[官方SDK出版许可说明](https://www.live2d.com/en/sdk/license/)区分一般内容与Expandable Application；可添加任意模型的助手是否落入后者、个体/小规模豁免是否适用，仍需Live2D确认，不能自行下结论。此次只记录，不购买、不申请授权、不接受条款代替用户操作。协议翻译不能取代其约定的日文效力文本。

## 实际安装与构建（macOS，本机验证使用Web 5-r.5）

1. 从[官方Cubism SDK for Web下载页](https://www.live2d.com/en/sdk/download/web/)自行获取SDK，阅读并接受适用条款；解压到自选目录。SDK目录需包含`Core/live2dcubismcore.min.js`、`Framework/src`、`Framework/Shaders/WebGL`及随包LICENSE/NOTICE/CHANGELOG。本项目不下载、不镜像SDK。
2. 在项目根安装Python依赖、在live2d安装构建依赖（不会下载Qwen）：

```sh
.venv/bin/python -m pip install -r requirements.txt
cd live2d
npm ci
../.venv/bin/python prepare.py --sdk /path/to/CubismSdkForWeb-5-r.5 --asuka
npm run build
cd ..
.venv/bin/python launch_desktop.py
```

`--asuka`使用随库v7资源及0.88比例/1.6发束输出配置；使用前先阅读素材NOTICE。`--model /path/to/your.model3.json`仍可选择自己的模型，与`--asuka`互斥。prepare仅复制指定SDK和引用文件到本机忽略目录，原样保留版权头，SDK的LICENSE/NOTICE及各组件LICENSE留在`.sdk`；不把SDK授权证明伪造成自动通过。

若不安装SDK或不构建Live2D，`launch_desktop.py`继续使用蓝色原生回退；网页聊天/文件助手也不依赖角色资源。构建后的`.sdk/.assets/.runtime`及bundle仍不提交。其他SDK版本、Windows和替代角色动作绑定未在此次验证。应用完整发布许可适用性不由构建成功证明。
