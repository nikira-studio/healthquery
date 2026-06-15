from __future__ import annotations


class HealthQueryError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict[str, object]:
        return {"error": self.message, "status_code": self.status_code}
