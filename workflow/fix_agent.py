from .coding_agent import CodingAgent
from .prompts import FIX_PROMPT

class FixAgent(CodingAgent):
    prompt = FIX_PROMPT

    def run(self, state):
        state.code = self._generate({"requirement": state.requirement, "code": state.code,
                               "review": state.review_result.to_dict()})
        return state

