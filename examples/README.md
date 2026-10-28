# 虚构样例

发行包的 Samples 包含虚构品牌 Guide、标准 LOGO、定稿文案和含 3 处已知问题的长图。源码仓库不保存二进制素材，可在 Windows 安装依赖后自行生成：

```powershell
.\.venv\Scripts\python.exe tests\fixtures.py --samples TestResults\Samples
```

已知问题为 LOGO 拉伸、日期不一致和一个错字。离线测试用固定响应验证程序流程；只有实际调用视觉模型才能了解该模型会发现哪些问题，真实调用可能计费。不要将测试固定响应描述为模型准确率。
