"""Agent core module."""

try:
    from nanobot.agent.context import ContextBuilder
    from nanobot.agent.loop import AgentLoop
    from nanobot.agent.memory import MemoryStore
    from nanobot.agent.skills import SkillsLoader
except ImportError:
    ContextBuilder = None  # type: ignore[assignment,misc]
    AgentLoop = None  # type: ignore[assignment,misc]
    MemoryStore = None  # type: ignore[assignment,misc]
    SkillsLoader = None  # type: ignore[assignment,misc]

__all__ = ["AgentLoop", "ContextBuilder", "MemoryStore", "SkillsLoader"]
