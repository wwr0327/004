"""Read-only workspace tools and conservative Python AST checks."""
import ast
import json
from pathlib import Path


SCHEMAS = [
    {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False}}}
    for name, description, properties, required in [
        ("list_files", "列出工作目录内可读取的代码和文档文件（最多 200 个）。", {}, []),
        ("read_file", "读取 UTF-8 文本文件，返回带行号的指定范围（最多 200 行）。",
         {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1},
          "end_line": {"type": "integer", "minimum": 1}}, ["path"]),
        ("analyze_python", "通过 AST 检查 Python 语法及常见风险，不执行代码。",
         {"path": {"type": "string"}}, ["path"]),
    ]
]


class WorkspaceTools:
    SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp",
                ".h", ".go", ".rs", ".md", ".txt", ".json", ".toml", ".yaml", ".yml"}
    SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    MAX_BYTES = 128 * 1024

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("工作目录必须是文件夹")

    def _path(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("path 必须是非空字符串")
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("只允许工作目录内的相对路径")
        resolved = (self.root / candidate).resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("路径超出工作目录（包括符号链接）")
        relative = resolved.relative_to(self.root)
        if any(p.startswith(".") or p in self.SKIP for p in relative.parts):
            raise ValueError("不读取隐藏文件或依赖目录")
        if not resolved.is_file() or resolved.suffix.lower() not in self.SUFFIXES:
            raise ValueError("仅支持允许的代码和文本文件")
        return resolved

    def _text(self, path):
        with self._path(path).open("rb") as stream:
            data = stream.read(self.MAX_BYTES + 1)
        if len(data) > self.MAX_BYTES:
            raise ValueError("文件超过 128 KiB，请缩小审查范围")
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("不支持二进制文件")
        return text

    def list_files(self):
        import os
        found = []
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in self.SKIP
                             and not (Path(directory) / d).is_symlink())
            for name in sorted(files):
                relative = (Path(directory) / name).relative_to(self.root).as_posix()
                try:
                    self._path(relative)
                except (OSError, ValueError):
                    continue
                found.append(relative)
                if len(found) > 200:
                    return {"files": found[:200], "truncated": True}
        return {"files": found, "truncated": False}

    def read_file(self, path, start_line=1, end_line=None):
        if type(start_line) is not int or start_line < 1:
            raise ValueError("start_line 必须是正整数")
        if end_line is None:
            end_line = start_line + 199
        if type(end_line) is not int or end_line < start_line or end_line - start_line >= 200:
            raise ValueError("行范围无效，一次最多读取 200 行")
        lines = self._text(path).splitlines()
        if start_line > max(1, len(lines)):
            raise ValueError("起始行超出文件范围")
        selected = lines[start_line - 1:end_line]
        content = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start_line))
        if len(content) > 16000:
            raise ValueError("所选内容过长，请缩小行范围")
        return {"path": path, "total_lines": len(lines), "content": content,
                "has_more": end_line < len(lines)}

    def analyze_python(self, path):
        if Path(path).suffix.lower() != ".py":
            raise ValueError("AST 分析仅支持 .py 文件")
        text = self._text(path)
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            return {"path": path, "syntax_ok": False, "findings": [
                {"rule": "syntax", "line": exc.lineno, "severity": "high",
                 "message": exc.msg, "suggestion": "修复语法后重新分析"}]}
        findings = []

        def add(node, rule, message, suggestion, severity="medium"):
            findings.append({"rule": rule, "line": node.lineno, "severity": severity,
                             "message": message, "suggestion": suggestion})

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = node.args.defaults + [d for d in node.args.kw_defaults if d is not None]
                if any(isinstance(d, (ast.List, ast.Dict, ast.Set)) for d in defaults):
                    add(node, "mutable-default", "可变默认参数会在多次调用间共享状态",
                        "使用 None 作为默认值，在函数内部创建容器")
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                add(node, "bare-except", "裸 except 会捕获退出和中断等异常",
                    "捕获具体异常类型，并保留可诊断信息")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                add(node, "dynamic-execution", f"检测到 {node.func.id} 调用，外部输入可能导致代码执行",
                    "核实输入来源，按数据格式使用 json.loads 等解析方式", "high")
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(node.right, ast.Constant) and node.right.value == 0:
                    add(node, "zero-division", "除数为字面量 0，执行到此处会抛出异常",
                        "修正除数，并处理零值输入", "high")
        findings.sort(key=lambda x: x["line"])
        return {"path": path, "syntax_ok": True, "findings": findings[:100],
                "truncated": len(findings) > 100,
                "note": "启发式静态检查，可能误报或漏报；没有执行代码。"}

    def execute(self, name, arguments):
        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            allowed = {"list_files": self.list_files, "read_file": self.read_file,
                       "analyze_python": self.analyze_python}
            if name not in allowed:
                raise ValueError(f"未知工具：{name}")
            return {"ok": True, "result": allowed[name](**args)}
        except (ValueError, TypeError, OSError, RecursionError) as exc:
            return {"ok": False, "error": str(exc)}
