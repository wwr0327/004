"""Every workflow node reads and returns the same shared state."""
from abc import ABC, abstractmethod
from .state import WorkflowState


class BaseAgent(ABC):
    @abstractmethod
    def run(self, state: WorkflowState) -> WorkflowState:
        """Update only the node's output fields and return state."""
        raise NotImplementedError
