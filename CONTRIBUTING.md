# 开发与构建

使用 Windows x64 Python 3.14，在 `.venv` 安装 `requirements.txt`。前端是原生 HTML/CSS/JavaScript，无 Node 构建依赖。业务代码在 `app`，离线测试在 `tests`；测试只用虚构素材，输出在忽略的 `TestResults`。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\check_release.py
# 可选：已安装 Node 时检查前端语法
node --check app\static\app.js
# 下载固定官方运行时/依赖，并生成发行包及校验值
py -3.14 tools\build_portable.py --output dist
```

构建工具接受 `--cache <目录>` 复用已下载且哈希正确的文件。`tools/distribution-lock.json` 固定 Python 嵌入式发行包和 Windows wheel 的 URL、版本与 SHA-256；更新时先核验官方来源和许可证，再做完整离线、便携启动与升级验证。`dist` 和缓存不进入源码 Git。Python 及 wheel 自带许可证在发行包内保留。

提交前检查 `git diff --cached`。不得提交 Data、ServerData、ServerConfig.json、Key、日志、真实输入或机器专属路径。需要改数据结构时必须保留备份迁移和历史快照，接口失败不得增加默认自动重试或自动付费兜底。发布需要维护者明确授权；本仓库 CI 只测试，不自动发布。
