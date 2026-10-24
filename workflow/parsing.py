import json
import re
from code_review_agent.llm import ModelError

def unfence(text):
    text = text.strip()
    match = re.fullmatch(r"```(?:python|py|json)?\s*\n(.*?)\n```", text, re.DOTALL)
    return match.group(1).strip() if match else text


def parse_review(text):
    try:
        review = json.loads(unfence(text))
    except (ValueError, TypeError, AttributeError):
        raise ModelError("审查结果不是有效 JSON") from None
    if (not isinstance(review, dict) or type(review.get("approved")) is not bool
            or not isinstance(review.get("summary"), str) or not review["summary"].strip()
            or not isinstance(review.get("issues"), list)):
        raise ModelError("审查结果缺少 approved、summary 或 issues")
    for issue in review["issues"]:
        if (not isinstance(issue, dict)
                or issue.get("severity") not in ("high", "medium", "low")
                or type(issue.get("line")) is not int or issue["line"] < 1
                or any(not isinstance(issue.get(k), str) or not issue[k].strip()
                       for k in ("message", "suggestion"))):
            raise ModelError("审查问题的字段格式无效")
    if review["approved"] == bool(review["issues"]):
        raise ModelError("审查通过标记与问题列表矛盾")
    return review

