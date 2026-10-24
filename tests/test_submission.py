from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.package_submission import package


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("main.py", "README.md", "Design.md", ".env.example"):
            (self.root / name).write_text("# example", encoding="utf-8")
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=local-test-secret", encoding="utf-8")

    def test_excludes_env_history_runs_and_caches(self):
        for folder in ("runs", ".git", "workflow/__pycache__"):
            directory = self.root / folder
            directory.mkdir(parents=True)
            (directory / "private.txt").write_text("local-test-secret")
        result = package(self.root, self.root / "dist", "10086测试")
        with zipfile.ZipFile(result) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertIn("10086测试/source/.env.example", names)
            self.assertIn("10086测试/Design.md", names)
            self.assertFalse(any(name.endswith("/.env") or "/runs/" in name or "/.git/" in name for name in names))
            self.assertFalse(any(b"local-test-secret" in archive.read(name) for name in names))

    def test_refuses_secret_in_source_and_overwriting_archive(self):
        (self.root / "main.py").write_text("local-test-secret")
        with self.assertRaisesRegex(ValueError, "密钥"):
            package(self.root, self.root / "dist")
        self.assertFalse((self.root / "dist").exists())
        (self.root / "main.py").write_text("# example")
        package(self.root, self.root / "dist")
        with self.assertRaises(FileExistsError):
            package(self.root, self.root / "dist")

    def test_invalid_name(self):
        with self.assertRaises(ValueError):
            package(self.root, self.root / "dist", "../escape")
