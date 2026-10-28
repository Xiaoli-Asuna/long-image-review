# 校样 · 长图视觉审稿

An API-first visual proofreading workspace for Chinese long-form images, with a Windows office LAN edition.

将长期项目的视觉 Guide 与每篇稿件的文案分开管理，帮助编导、运营和设计在人工终审前发现长图中的文字、LOGO 和规范疑点。源码使用 MIT 许可证。

## 开始使用

- **办公室共用：**在 Windows 10/11 x64 服务器下载 [最新正式版](../../releases/latest) 的 `LongImageReview-v1.0.0-windows-x64.zip`，完整解压后运行 `启动办公室服务器.cmd`。首次选择办公室网卡，在服务器本机浏览器创建管理员，然后由管理员配置视觉 API。同事通过局域网地址自行注册用户名、密码和职位（编导、运营、设计）。
- **本机体验：**完整解压后运行 `启动校样.cmd`，浏览器打开单机工作台；无需安装 Python 或 Node。
- **交给其他 AI 部署：**直接复制 [DEPLOY_WITH_AI.md](DEPLOY_WITH_AI.md) 中的提示词，并提供本仓库地址。
- **从源码启动：**见下方命令及 [完整部署说明](docs/DEPLOYMENT.md)。

```powershell
# 在仓库根目录执行；需要预先安装 Windows x64 Python 3.14
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Launch.cmd
# 办公室版使用 .\StartServer.cmd
```

启动器优先使用发行包内的 `runtime`，源码安装时回退到 `.venv`。源码本身不包含运行时、模型权重、账号、API Key 或真实客户素材。

## 工作流程

1. **建立长期项目。**上传视觉 Guide；已配置 API 时自动解析规范和 LOGO 候选，人工核对来源、正例/禁用分类后确认启用。后续活动复用规范。
2. **每次活动新建稿件。**填写定稿文案与本篇设计要求，或导入 XLSX 需求表，选择工作表及真正需要上版的单元格。项目共用规范不随文案改变。
3. **设计选择项目和稿件送审。**上传 JPG/PNG 长图；程序分片识别文字、对照文案，并将已确认规范用于视觉检查。
4. **人工处理疑点。**查看原图位置、依据和调用记录，标记处理意见；修改后再次送审，历史报告保留当时资料快照，可导出独立 HTML 报告。

Guide 支持 PDF、图片、TXT/MD、DOCX；LOGO 从 PDF/图片原页裁切，保留背景。XLSX 只读取单元格和公式缓存，不执行公式或外链。一次审核一张静态长图，最多 24 个切片，具体大小限制见界面。

## API 和费用

管理员填写 OpenAI 兼容的 Base URL、**支持图片输入**的模型 ID 和自己的 Key。兼容接口需支持 `chat/completions`、图片 data URL 和 JSON 输出；可选择流式接收。读取模型列表的“测试连接”不验证余额、图片推理或审核质量。

上传/重新解析 Guide，以及点击设计审核，会将必要文字和图片发送给所配置服务，可能产生该服务商的费用。程序不自动重试失败任务、不自动切换收费模型；用量缺失时显示未知。以后可切换到自建 Qwen **视觉模型**的兼容端点，API 模式无需显卡；本项目不包含模型安装器或模型权重。

## 办公室版的边界

- 目标环境为可信办公室局域网；默认 HTTP，不应将端口直接暴露到公网。上传资料集中保存在服务器 `ServerData`，所有已登录成员共享项目库，**没有项目级隔离**。
- 员工自助注册无验证码、邀请码或审批；管理员只能在首次启动时通过服务器本机初始化。管理员管理账号，编导/运营维护资料，设计送审；模型设置仅管理员可改，HTTP 下须在服务器本机操作。
- 密码存储为带盐 PBKDF2 摘要。Key 默认只保留在进程内；选择记住时由当前 Windows 用户的 DPAPI 加密。备份或换电脑仍需重新填写 Key。
- `允许办公室访问.cmd` 仅在主动运行并同意系统提权后配置 Private/Domain、LocalSubnet 防火墙规则。`InstallStartup.cmd` 是**当前 Windows 用户登录后启动**，不是登录前的系统服务。

更多数据流与备份说明见 [隐私说明](docs/PRIVACY.md)、[部署说明](docs/DEPLOYMENT.md) 和 [安全边界](SECURITY.md)。

## 验证和限制

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\check_release.py
# 生成虚构品牌样例，不调用模型
.\.venv\Scripts\python.exe tests\fixtures.py --samples TestResults\Samples
```

离线测试使用虚构素材和固定 HTTP/SSE 响应，检查文案核对、Guide 来源、报告快照、迁移、权限、并发冲突、注册与流式完整性，**不代表真实模型准确率**。正式版还检查 Windows 免安装启动、重启持久化和旧版虚构资料升级；未验证某台实际办公室服务器的网卡、防火墙、登录任务或收费模型效果。

AI 结果仅供初审；艺术字、小字、标点、复杂阅读顺序及日期仍需人工复核。位图无法认证字体文件及其授权。当前面向静态 JPG/PNG，不覆盖互动 SVG 状态、GIF 各帧或音频。Windows x64 是本版部署目标；其他操作系统未通过发行验收。

## 构建发行包

```powershell
py -3.14 tools\build_portable.py --output dist
```

构建器仅从 `tools/distribution-lock.json` 中的官方地址下载固定版本并验证 SHA-256，白名单打包源码、文档、运行时和依赖，生成 ZIP、逐文件校验清单和 ZIP 校验值。详见 [贡献说明](CONTRIBUTING.md)。
