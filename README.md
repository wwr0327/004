# Homework 2：多 Agent 代码审查 Workflow

按新版 Homework 2 要求实现的 Python MVP。三个 Agent 通过共享状态完成 **编码 → 审查 → 修复 → 复审**，支持条件分支、有界循环和持久化日志。复用 Homework 1 的 ReviewAgent、文件工具与模型客户端。

## 一条命令演示

需要 Python 3.10+，仅使用标准库。在本 README 同级的项目目录运行：

```powershell
python main.py
```

无参数时自动使用固定离线 Calculator 演示，不联网、不读取 API Key。终端依次展示编码、审查发现除零异常不符合需求、修复、复审通过，并打印结果目录。离线演示使用预设响应，文件读取、AST 检查、状态共享和结果保存实际执行。

## 使用已有 API Key

本地已有 `.env` 可以直接使用，无需重新填写或覆盖。新下载项目的用户仅在 `.env` 不存在时创建它：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

在 `.env` 填写 `DEEPSEEK_API_KEY`。不要提交此文件，仓库和提交包只包含 `.env.example`。

```powershell
# 真实任务，使用已有配置
python main.py --verbose "实现 Calculator，支持加减乘除，除零抛出 ValueError"

# 查询可用模型、测试聊天连接
python main.py --provider deepseek --list-models
python main.py --check-connection

# 选择模型后执行任务
python main.py --provider deepseek --select-model --verbose "实现 count_words(text)，统计英文单词频次，忽略大小写"

# 指定模型、修复上限和结果目录
python main.py --provider deepseek --model deepseek-flash --max-fixes 2 --output-dir runs --verbose "实现保持顺序的字符串列表去重函数"

# 本地 Ollama 服务需预先启动并部署支持工具调用的模型
python main.py --provider ollama --select-model "实现字符串去重函数"

# custom 使用独立的 LLM_API_KEY；地址和模型填写服务提供的值
python main.py --provider custom --base-url https://your-service.example/v1 --model YOUR_MODEL "实现字符串去重函数"
```

模型需支持 Chat Completions、函数工具调用及 JSON 输出。模型列表或聊天测试成功不代表完整流程可用。DeepSeek 使用 DEEPSEEK_API_KEY（兼容 LLM_API_KEY），custom 只使用 LLM_API_KEY，本地 Ollama 不需要真实 Key；远程 Ollama 使用 OLLAMA_API_KEY。专用密钥不会转发给其他服务。

命令行参数优先于环境变量，环境变量优先于 `.env`，最后使用默认值。切换 provider 时不继承旧服务的地址和模型。`--thinking enabled/disabled` 控制 DeepSeek 思考模式。`--demo` 与模型连接选项互斥。

## 执行结果

每次运行创建独立的 `runs/时间-随机编号/`：

| 文件 | 内容 |
| --- | --- |
| revision_0.py | 初稿 |
| revision_1.py 等 | 各次修复后的代码 |
| final.py | 最后一个完成审查的版本 |
| report.md | 需求、阶段记录、各轮审查问题 |
| state.json | 源码、ReviewResult、历史审查、计数及共享状态 |
| workflow.log | 每行一条 JSON 日志，包含 UTC 时间、Agent、状态、版本和工具调用 |

默认最多修复 **2 次**，最多审查 **3 轮**；`--max-fixes` 可设为 0–5。首次审查通过就跳过 FixAgent。每次修复后必须重新审查，达到上限时不会直接宣告成功。

| 状态 | 退出码 | 含义 |
| --- | --- | --- |
| completed | 0 | 当前代码审查通过 |
| needs_attention | 2 | 修复次数用尽，仍有问题 |
| failed | 1 | 模型、格式或文件操作失败 |
| cancelled | 130 | 用户取消 |

本程序不执行模型生成的代码或测试。`final.py` 在 needs_attention 时仍可能有问题，使用前应查看报告。失败时尽力保存已有状态；不支持断点自动恢复。真实模式会将需求与生成代码发给选定模型服务。

## 三个 Agent 与共享状态

| Agent | 输入 | 输出与职责 |
| --- | --- | --- |
| CodingAgent | requirement | 生成初稿，写入 state.code |
| ReviewAgent | requirement、code、文件工具证据 | 写入结构化 review_result 与审查历史 |
| FixAgent | requirement、code、review_result | 修复代码，更新 state.code |

Agent 均实现 `run(state) -> state`，不相互调用。WorkflowEngine 控制执行顺序和终止条件。详细流程图、状态协议及扩展方式见 [Design.md](Design.md)。

## 项目结构

```text
main.py                 一键演示与真实任务入口
workflow/
  state.py              WorkflowState、ReviewResult
  base_agent.py         统一节点接口
  coding_agent.py       编码节点
  review_agent.py       旧审查 Agent 的适配节点
  fix_agent.py          修复节点
  engine.py             顺序、分支、循环、保存与日志
  cli.py                命令行参数与模型选择
  parsing.py            输出解析与校验
  prompts.py            角色提示与演示需求
code_review_agent/      Homework 1 工具、客户端及旧入口
examples/requirements.txt  示例需求
scripts/package_submission.py  安全打包脚本
tests/                  回归、状态、编排与打包测试
```

## 旧命令兼容

```powershell
python -m workflow --demo --verbose
python -m code_review_agent.workflow --demo --verbose
python -m code_review_agent --verbose "请审查 examples/buggy.py"
python -m code_review_agent --root "C:\path\to\project" "审查 main.py"
```

旧审查入口仍提供只读审查、多轮聊天和 `/reset`、`/exit`。工作流入口保留原参数；Python 节点 API 已统一为返回 WorkflowState。

