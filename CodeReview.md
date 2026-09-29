# 代码复查与验证记录

日期：2026-09-29。修改范围仅为 928/homework2。

## 已修复问题

| 问题 | 影响 | 修改 |
| --- | --- | --- |
| 配置固定从 .env 读取 | 切换服务和模型不方便 | 两个 CLI 共用 --provider、--model、--base-url、--list-models、--select-model、--check-connection |
| DeepSeek 密钥始终优先 | 自定义地址可能误收其他服务的专用密钥 | provider 隔离密钥；DeepSeek profile 限定官方主机 |
| 审查系统提示要求文字报告，工作流要求 JSON | 真实模型可能返回无法解析的结果 | 工作流使用独立 JSON 系统提示 |
| 模型可跳过读文件直接给出通过结论 | 缺少源码证据 | 每轮审查上下文预先包含完整源码及实际 AST 结果 |
| 服务返回非对象错误或 choice 为 null | AttributeError 导致非预期退出 | 验证结构并转换为 ModelError，保留错误脱敏 |
| severity 返回数组或对象 | 集合成员判断可能抛出 TypeError | 使用兼容非法类型的验证逻辑并报格式错误 |

## 验证结果

- 52 项自动测试：51 项通过，1 项因 Windows 符号链接权限跳过。
- 离线完整流程通过：编码、审查、修复、复审、保存状态。
- 使用本地已有配置完成真实 DeepSeek 聊天连接测试，未显示或修改密钥。
- 实际获取模型列表，返回 deepseek-flash、deepseek-v4-pro。列表随账号和服务变化，以运行时结果为准。
- 真实 deepseek-flash 工作流完成 Calculator 任务。初稿含 Markdown 标记导致语法错误，审查和 AST 检测均发现问题；FixAgent 修复后复审通过。
- 真实运行证据位于 runs/20260929-112530-b3e13c3f3a44/report.md 和 state.json。

## 当前边界

真实联调覆盖 DeepSeek；Ollama 和 custom 仅通过模拟协议与配置测试，尚未连接实际部署服务。连接测试只验证聊天请求，不验证工具能力。模型结果仍可能有误，程序未执行生成代码或生成测试。磁盘不可写时无法保证保存状态；目前不支持断点恢复。

模型列表接口参考：[DeepSeek](https://api-docs.deepseek.com/api/list-models/)。本地服务地址及兼容协议参考：[Ollama](https://docs.ollama.com/api/openai-compatibility)。
