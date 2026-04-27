from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    message: str
    path: Path | None = None
    location: str | None = None

    def render(self) -> str:
        prefix = ""
        if self.path is not None:
            prefix += str(self.path)
        if self.location:
            prefix += f":{self.location}"
        if prefix:
            return f"{prefix}: {self.message}"
        return self.message


class KschError(Exception):
    def __init__(self, message: str, path: Path | None = None, location: str | None = None):
        self.diagnostic = Diagnostic(message=message, path=path, location=location)
        super().__init__(self.diagnostic.render())
