from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ksch.model.source import PinDirection, SymbolDecl


class ChildInstanceIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    source: Path
    target_path: str


class SheetIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    source_path: Path
    title: str | None = None
    interface: dict[str, PinDirection] = Field(default_factory=dict)
    symbols: dict[str, SymbolDecl] = Field(default_factory=dict)
    nets: dict[str, list[str]] = Field(default_factory=dict)
    no_connects: list[str] = Field(default_factory=list)
    assertions: list[dict[str, object]] = Field(default_factory=list)
    child_instances: dict[str, ChildInstanceIR] = Field(default_factory=dict)


class ProjectIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    root_path: Path
    sheets: dict[str, SheetIR]
    symbol_libraries: dict[str, Path] = Field(default_factory=dict)
