from .base import BaseAgent
from .prompts import WRITER_SYSTEM


class WriterAgent(BaseAgent):
    name = "writer"
    system_prompt = WRITER_SYSTEM
    max_memory: int = 16   # Writer 每节独立调用，短历史即可，防 context 膨胀
