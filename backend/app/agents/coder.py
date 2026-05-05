from .base import BaseAgent
from .prompts import CODER_SYSTEM


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = CODER_SYSTEM
