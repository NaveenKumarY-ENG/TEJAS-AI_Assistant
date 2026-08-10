"""
Every tool is a self-contained plugin that implements this interface.
Drop a new file in tools/, subclass Tool, register it in tools/__init__.py — done.
"""
from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Base class all tools must implement."""

    name: str = "unnamed_tool"
    description: str = "No description provided."
    # JSON schema for the tool's input, following Anthropic's tool-use format
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def run(self, **kwargs: Any) -> str:
        """
        Execute the tool and return a string result.
        Must never raise — catch internally and return a descriptive error string,
        so a single bad tool call can't crash the agent loop.
        """
        raise NotImplementedError

    def to_schema(self) -> dict:
        """Convert to the schema format the Claude API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }