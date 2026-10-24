"""Explicit shared state and typed review result for workflow nodes."""
from dataclasses import asdict, dataclass, field


@dataclass
class ReviewResult:
    passed: bool
    summary: str
    issues: list = field(default_factory=list)
    static_analysis: dict = field(default_factory=dict)
    revision: int = 0

    @classmethod
    def from_dict(cls, value):
        import json
        from .parsing import parse_review
        value = parse_review(json.dumps(value, ensure_ascii=False))
        return cls(value["approved"], value["summary"], value["issues"],
                   value.get("static_analysis", {}), value.get("revision", 0))

    def to_dict(self):
        result = asdict(self)
        result["approved"] = result.pop("passed")
        return result


@dataclass
class WorkflowState:
    requirement: str
    run_dir: str = ""
    status: str = "pending"
    stage: str = "pending"
    revision: int = 0
    iteration: int = 0
    code: str = ""
    code_path: str = ""
    final_code: str = ""
    review_result: ReviewResult | None = None
    reviews: list = field(default_factory=list)
    events: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    error: str = ""

    @property
    def current_step(self):
        return self.stage

    @current_step.setter
    def current_step(self, value):
        self.stage = value

    @property
    def review_passed(self):
        return self.review_result is not None and self.review_result.passed

    def to_dict(self):
        result = asdict(self)
        result.update(current_step=self.current_step, review_passed=self.review_passed)
        return result
