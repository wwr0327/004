from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from code_review_agent.llm import ModelError
from .state import WorkflowState
from .coding_agent import CodingAgent
from .fix_agent import FixAgent
from .review_agent import ReviewAgent

class WorkflowEngine:
    def __init__(self, client, output_dir="runs", max_fixes=2, trace=None,
                 coding_agent=None, fix_agent=None, review_agent=None):
        if type(max_fixes) is not int or not 0 <= max_fixes <= 5:
            raise ValueError("max_fixes 必须在 0 到 5 之间")
        self.client, self.output_dir = client, Path(output_dir)
        self.max_fixes = max_fixes
        self.trace = trace or (lambda event: None)
        self.reviewer = review_agent
        self.coder = coding_agent or CodingAgent(client)
        self.fixer = fix_agent or FixAgent(client)

    def _save(self, state):
        root = Path(state.run_dir)
        temporary = root / "state.tmp"
        temporary.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(root / "state.json")

    def _log(self, state, agent, message, status):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "agent": agent,
                 "status": status, "revision": state.revision, "message": message}
        state.logs.append(entry)
        with (Path(state.run_dir) / "workflow.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._save(state)
        self.trace(f"{agent} [{status}]: {message}")

    def _best_effort_save(self, state):
        state.logs.append({"time": datetime.now(timezone.utc).isoformat(),
                           "agent": state.stage, "status": state.status,
                           "revision": state.revision, "message": state.error})
        try:
            self._save(state)
            with (Path(state.run_dir) / "workflow.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(state.logs[-1], ensure_ascii=False) + "\n")
        except OSError:
            self.trace("无法保存检查点，请检查目录权限或可用磁盘空间")

    @staticmethod
    def _run_node(node, state):
        # Keep state transactional: failed nodes cannot corrupt the last checkpoint.
        from copy import deepcopy
        draft = deepcopy(state)
        if state.stage == "review":
            draft.review_result = None
        result = node.run(draft)
        if result is not draft:
            raise ValueError("Agent.run 必须返回传入的 WorkflowState")
        if draft.requirement != state.requirement or draft.run_dir != state.run_dir:
            raise ValueError("Agent 不得修改任务需求或输出目录")
        # Tool traces are emitted by the engine while the node works on its draft.
        draft.logs, draft.events = state.logs, state.events
        state.__dict__.update(draft.__dict__)

    def _event(self, state, stage, message):
        state.stage = stage
        state.events.append({"time": datetime.now(timezone.utc).isoformat(),
                             "stage": stage, "revision": state.revision, "message": message})
        self._log(state, stage, message, "running" if state.status == "running" else state.status)

    def _write_code(self, state, code):
        if not isinstance(code, str) or not code.strip() or len(code) > 32001:
            raise ValueError("Agent 必须提供有效的 Python 源码，最多 32000 字符")
        name = f"revision_{state.revision}.py"
        with (Path(state.run_dir) / name).open("x", encoding="utf-8") as stream:
            stream.write(code)
        state.code, state.code_path = code, name
        self._save(state)

    def run(self, requirement):
        if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 8000:
            raise ValueError("需求不能为空，且不能超过 8000 字符")
        root = self.output_dir / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:12])
        root.mkdir(parents=True, exist_ok=False)
        state = WorkflowState(requirement.strip(), str(root.resolve()), status="running")
        reviewer = self.reviewer or ReviewAgent(self.client, root,
            lambda message: self._log(state, "review", message, "tool"))
        try:
            self._event(state, "coding", "生成初始代码")
            self._run_node(self.coder, state)
            self._write_code(state, state.code)
            self._log(state, "coding", "初稿已保存", "completed")
            while True:
                self._event(state, "review", "审查当前代码")
                self._run_node(reviewer, state)
                if state.review_result is None:
                    raise ValueError("审查节点必须写入 review_result")
                self._log(state, "review", state.review_result.summary, "completed")
                if state.review_passed:
                    state.status = "completed"
                    break
                if state.revision >= self.max_fixes:
                    state.status = "needs_attention"
                    break
                self._event(state, "fix", "根据需求和审查结果修复")
                self._run_node(self.fixer, state)
                state.revision += 1
                self._write_code(state, state.code)
                self._log(state, "fix", "修复代码已保存，等待复审", "completed")
            state.final_code = state.code
            (root / "final.py").write_text(state.final_code, encoding="utf-8")
            self._event(state, "done", state.status)
        except (ModelError, OSError, ValueError) as exc:
            state.status, state.error = "failed", str(exc)
            self._best_effort_save(state)
            self.trace("失败：" + state.error)
        except KeyboardInterrupt:
            state.status, state.error = "cancelled", "用户取消"
            self._best_effort_save(state)
            raise
        try:
            self._report(state)
        except OSError as exc:
            state.status, state.error = "failed", state.error or str(exc)
            self._best_effort_save(state)
        return state

    def _report(self, state):
        lines = ["# Workflow 执行报告", "", f"状态：{state.status}", "",
                 "## 原始需求", state.requirement, "", "## 执行记录"]
        lines.extend(f"- {e['stage']} / 版本 {e['revision']}：{e['message']}" for e in state.events)
        for review in state.reviews:
            lines.extend(["", f"## 版本 {review['revision']} 审查", review["summary"]])
            lines.extend(f"- [{i['severity']}] 第 {i['line']} 行：{i['message']}；{i['suggestion']}"
                         for i in review["issues"])
        if state.error:
            lines.extend(["", "错误：" + state.error])
        lines.extend(["", "本流程仅生成代码并进行静态及模型审查，未执行生成代码或其测试。",
                      "completed 表示审查通过；needs_attention 表示修复次数用尽后仍有问题。"])
        (Path(state.run_dir) / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

