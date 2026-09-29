import argparse
from pathlib import Path
import sys
from .agent import ReviewAgent
from .demo import DemoClient
from .llm import ChatClient, ModelError
from .tools import WorkspaceTools
from .model_options import add_model_options, validate_model_options, connect_model


def _configure_utf8_console():
    """Prevent model output such as emoji from crashing a legacy Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv=None):
    _configure_utf8_console()
    parser = argparse.ArgumentParser(description="Homework 1 代码审查 Agent")
    parser.add_argument("question", nargs="?", help="省略时进入多轮交互")
    parser.add_argument("--root", default=".", help="允许读取的工作目录")
    parser.add_argument("--demo", action="store_true", help="固定离线演示，无需 API Key")
    parser.add_argument("--verbose", action="store_true", help="显示决策和工具调用阶段")
    parser.add_argument("--output", help="将单次回答保存为 Markdown（文件必须尚不存在）")
    add_model_options(parser)
    args = parser.parse_args(argv)
    validate_model_options(parser, args)
    if args.output and not (args.question or args.demo):
        parser.error("--output 需要单次问题或 --demo")
    trace = (lambda event: print(f"[Agent] {event}", file=sys.stderr)) if args.verbose else None
    try:
        tools = WorkspaceTools(args.root)
        if args.demo:
            client = DemoClient()
        else:
            client, done = connect_model(args)
            if done:
                return 0
        agent = ReviewAgent(client, tools, trace=trace)
        if args.demo:
            print("离线演示模式：使用固定模拟响应，不连接真实模型。", file=sys.stderr)
        if args.question or args.demo:
            result = agent.ask(args.question or "审查 examples/buggy.py")
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as target:
                    target.write(result + "\n")
            print(result)
            return 0
        print("代码审查 Agent，输入 /exit 退出，/reset 清空记忆。")
        print(f"工作目录：{tools.root}\n所读取的代码会发送到配置的模型服务。")
        while True:
            try:
                question = input("\n你 > ").strip()
            except EOFError:
                break
            if question == "/exit":
                break
            if question == "/reset":
                agent.reset()
                print("已清空对话记忆。")
                continue
            if not question:
                continue
            try:
                print("\nAgent > " + agent.ask(question))
            except (ValueError, ModelError) as exc:
                print(f"错误：{exc}", file=sys.stderr)
        return 0
    except (OSError, ValueError, ModelError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
