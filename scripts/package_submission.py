"""Build an allowlisted submission archive without local credentials or run data."""
import argparse
from pathlib import Path
import re
import zipfile

ROOT_FILES = ("main.py", "README.md", "Design.md", "WorkflowDesign.md", "CodeReview.md",
              "DemoGuide.md", "requirements.txt", ".env.example", ".gitignore")
SOURCE_DIRS = ("code_review_agent", "workflow", "tests", "examples", "scripts")
SECRET_PATTERN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|"
                            rb"github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")


def package(root, output_dir, name="Homework2"):
    root = Path(root).resolve(strict=True)
    if not re.fullmatch(r"[\w\-\u4e00-\u9fff]{1,80}", name):
        raise ValueError("包名只允许字母、数字、中文、下划线和短横线")
    secrets = []
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            if any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET")) and len(value) >= 8:
                secrets.append(value.encode("utf-8"))
    candidates = [root / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        candidates.extend((root / directory).rglob("*"))
    contents = {}
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        if "__pycache__" in relative.parts or any(p.startswith(".") for p in relative.parts):
            if relative.as_posix() not in (".env.example", ".gitignore"):
                continue
        if relative.as_posix() not in ROOT_FILES and path.suffix not in (".py", ".md", ".txt"):
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError(f"源文件过大：{relative}")
        data = path.read_bytes()
        if SECRET_PATTERN.search(data) or any(secret in data for secret in secrets):
            raise ValueError(f"发现疑似密钥，已停止打包：{relative}")
        contents[relative.as_posix()] = data
    for required in ("main.py", "README.md", "Design.md", ".env.example"):
        if required not in contents:
            raise ValueError(f"缺少提交文件：{required}")
    if sum(map(len, contents.values())) > 190 * 1024 * 1024:
        raise ValueError("源文件总大小超过打包限制")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{name}.zip"
    # Exclusive mode avoids replacing an earlier submission by accident.
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in contents.items():
            archive.writestr(f"{name}/source/{relative}", data)
        for doc in ("README.md", "Design.md", "DemoGuide.md"):
            if doc in contents:
                archive.writestr(f"{name}/{doc}", contents[doc])
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成不含 .env 和运行数据的作业提交包")
    parser.add_argument("--name", default="Homework2", help="提交时填写学号姓名")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        result = package(root, root / "dist", args.name)
        print(f"提交包：{result}\n大小：{result.stat().st_size / 1024:.1f} KiB")
        return 0
    except (ValueError, OSError) as exc:
        print(f"打包失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
