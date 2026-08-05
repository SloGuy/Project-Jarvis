from abc import ABC, abstractmethod
import re
from typing import Any


class RouterCapability(ABC):
    """
    Base class for deterministic Jarvis router capabilities.
    """

    name: str
    endpoint: str
    patterns: tuple[str, ...]

    def match(self, message: str) -> dict[str, Any] | None:
        """
        Return extracted request parameters when this capability matches.
        Return None when it does not match.
        """
        for pattern in self.patterns:
            if re.search(pattern, message, flags=re.IGNORECASE):
                return {}

        return None

    @abstractmethod
    def execute(self, **parameters: Any) -> Any:
        """
        Retrieve the live data required by this capability.
        """
        raise NotImplementedError

    @abstractmethod
    def format_response(
        self,
        data: Any,
        **parameters: Any,
    ) -> str:
        """
        Convert capability data into a human-readable chat response.
        """
        raise NotImplementedError
