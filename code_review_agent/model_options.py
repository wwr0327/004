"""Shared model selection for both command-line entry points."""
import sys
from .llm import ChatClient, ModelError


def add_model_options(parser):
    group = parser.add_argument_group("模型连接")
    group.add_argument("--provider", choices=("deepseek", "ollama", "custom"), help="选择服务")
    group.add_argument("--model", help="模型 ID，优先于环境配置")
    group.add_argument("--base-url", help="兼容服务基础地址，不含 /chat/completions")
    group.add_argument("--thinking", choices=("enabled", "disabled"), help="DeepSeek 思考模式")
    modes = group.add_mutually_exclusive_group()
    modes.add_argument("--list-models", action="store_true", help="读取服务模型列表后退出")
    modes.add_argument("--select-model", action="store_true", help="从服务模型列表交互选择")
    group.add_argument("--check-connection", action="store_true", help="发送简短聊天请求测试连接后退出，可能计费")


def validate_model_options(parser, args):
    options = (args.provider, args.model, args.base_url, args.thinking, args.list_models,
               args.select_model, args.check_connection)
    if args.demo and any(options):
        parser.error("--demo 不连接模型，不能与模型连接选项一起使用")
    if args.list_models and args.check_connection:
        parser.error("--list-models 与 --check-connection 请分别使用")
    if args.select_model and args.model:
        parser.error("--select-model 与 --model 请二选一")


def connect_model(args):
    client = ChatClient.from_env(provider=args.provider, model=args.model,
                                 base_url=args.base_url, thinking=args.thinking,
                                 discovery=args.list_models or args.select_model)
    if args.list_models or args.select_model:
        models = client.list_models()
        if not models:
            raise ModelError("服务没有返回可用模型，请先部署模型或检查账号权限")
        for number, model in enumerate(models, 1):
            print(f"{number}. {model}")
        if args.list_models:
            return client, True
        try:
            selection = input("选择模型编号（q 取消）：").strip()
        except EOFError:
            raise ValueError("无法读取选择；非交互环境请使用 --model") from None
        if selection.lower() == "q":
            raise KeyboardInterrupt
        if not selection.isascii() or not selection.isdigit() or not 1 <= int(selection) <= len(models):
            raise ValueError("模型编号无效")
        client.model = models[int(selection) - 1]
    print(f"模型：{client.provider} / {client.model}", file=sys.stderr)
    if args.check_connection:
        reply = client.complete([{"role": "user", "content": "Reply with OK only."}], [])
        if reply.get("tool_calls") or not isinstance(reply.get("content"), str) or not reply["content"].strip():
            raise ModelError("服务连接成功，但没有返回有效聊天文本")
        print("连接测试通过：模型已返回聊天文本。此测试不验证工具调用或审查质量。")
        return client, True
    return client, False
