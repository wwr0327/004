import argparse
import sys
from code_review_agent.llm import ModelError
from code_review_agent.model_options import add_model_options, validate_model_options, connect_model
from .engine import WorkflowEngine
from .prompts import DEMO_REQUIREMENT

def main(argv=None):
    from code_review_agent.__main__ import _configure_utf8_console
    from code_review_agent.workflow_demo import WorkflowDemoClient
    _configure_utf8_console()
    parser = argparse.ArgumentParser(description="Homework 2：编码 → 审查 → 修复")
    parser.add_argument("requirement", nargs="?", help="生成单个 Python 模块的需求")
    parser.add_argument("--demo", action="store_true", help="固定 Calculator 离线演示")
    parser.add_argument("--output-dir", default="runs", help="运行结果的父目录")
    parser.add_argument("--max-fixes", type=int, default=2, help="修复次数，0–5，默认 2；每次修复后必须复审")
    parser.add_argument("--verbose", action="store_true")
    add_model_options(parser)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["--demo", "--verbose"]
    args = parser.parse_args(argv)
    validate_model_options(parser, args)
    if not args.demo and not args.requirement and not (args.list_models or args.check_connection):
        parser.error("需要提供需求，或使用 --demo")
    if args.demo and args.requirement and args.requirement != DEMO_REQUIREMENT:
        parser.error("--demo 仅支持固定 Calculator 需求，请省略需求参数")
    try:
        trace = (lambda event: print(f"[Workflow] {event}", file=sys.stderr)) if args.verbose else None
        if args.demo:
            print("离线演示：固定响应，实际读文件和 AST 检查，不连接模型。", file=sys.stderr)
        if args.demo:
            client = WorkflowDemoClient()
        else:
            client, done = connect_model(args)
            if done:
                return 0
        state = WorkflowEngine(client, args.output_dir, args.max_fixes, trace).run(args.requirement or DEMO_REQUIREMENT)
        print(f"状态：{state.status}\n审查轮数：{state.iteration} / 修复次数：{state.revision}\n结果目录：{state.run_dir}")
        if state.error:
            print(f"错误：{state.error}", file=sys.stderr)
        return {"completed": 0, "needs_attention": 2, "failed": 1}[state.status]
    except (OSError, ValueError, ModelError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
