from .base import BaseAgent
from .prompts import WRITER_SYSTEM


class WriterAgent(BaseAgent):
    name = "writer"
    system_prompt = WRITER_SYSTEM
