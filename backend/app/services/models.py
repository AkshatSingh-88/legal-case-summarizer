"""Internal service-layer models and caller context representations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class CallerContext:
    """Represents the resolved caller identity and ownership context for an API request."""
    user_id: str | None = None
    guest_session_id: str | None = None
    role: Literal["authenticated", "guest", "anonymous"] = "anonymous"

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None and self.role == "authenticated"

    @property
    def is_guest(self) -> bool:
        return self.guest_session_id is not None and self.role == "guest"
