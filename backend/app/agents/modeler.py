from .base import BaseAgent
from .prompts import MODELER_SYSTEM


class ModelerAgent(BaseAgent):
    name = "modeler"
    system_prompt = MODELER_SYSTEM
