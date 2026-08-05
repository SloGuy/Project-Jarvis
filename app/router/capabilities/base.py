from abc import ABC, abstractmethod
from typing import Any


class RouterCapability(ABC):
    """
    Base class for deterministic Jarvis router capabilities.
    """

    name: str
    endpoint: str
    patterns: tuple[str, ...]

    @abstractmethod
    def execute(self) -> Any:
        """
        Retrieve the live data required by this capability.
        """
        raise NotImplementedError

    @abstractmethod
    def format_response(self, data: Any) -> str:
        """
        Convert capability data into a human-readable chat response.
        """
        raise NotImplementedError
