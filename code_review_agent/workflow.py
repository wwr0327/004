"""Compatibility imports and CLI for the modular Homework 2 workflow."""
from workflow.state import WorkflowState, ReviewResult
from workflow.coding_agent import CodingAgent
from workflow.fix_agent import FixAgent
from workflow.review_agent import ReviewAgent as ReviewNode
from workflow.engine import WorkflowEngine as Workflow
from workflow.parsing import parse_review, unfence
from workflow.prompts import DEMO_REQUIREMENT, CODE_PROMPT, FIX_PROMPT
from workflow.cli import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
