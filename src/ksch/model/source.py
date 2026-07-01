from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PinDirection = Literal[
    "input",
    "output",
    "bidirectional",
    "tri_state",
    "passive",
    "power_in",
    "power_out",
]


class ProjectMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str | None = None
    kicad_version: str | None = None


class SheetMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str | None = None


class LibrarySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_global: bool = True
    project: dict[str, Path] = Field(default_factory=dict)

    @field_validator("project", mode="before")
    @classmethod
    def normalize_project_libraries(cls, value: object) -> object:
        if isinstance(value, list):
            return {Path(item).stem: item for item in value}
        return value


class Libraries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: LibrarySet = Field(default_factory=LibrarySet)
    footprints: LibrarySet = Field(default_factory=LibrarySet)


class SheetInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Path
    title: str | None = None
    connects: dict[str, str] = Field(default_factory=dict)


class SymbolDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lib: str
    value: str | None = None
    footprint: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    units: list[int] | None = None
    connects: dict[str, str] = Field(default_factory=dict)


class BlockDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: list[str] = Field(default_factory=list)


class UseDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block: str
    as_: str = Field(alias="as")
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ksch: Literal[1]
    project: ProjectMeta | None = None
    sheet: SheetMeta | None = None
    libraries: Libraries = Field(default_factory=Libraries)
    interface: dict[str, PinDirection] = Field(default_factory=dict)
    sheets: dict[str, SheetInstance] = Field(default_factory=dict)
    symbols: dict[str, SymbolDecl] = Field(default_factory=dict)
    power_flags: list[str] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    blocks: dict[str, BlockDecl] = Field(default_factory=dict)
    use: list[UseDecl] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document_kind(self) -> "SourceDocument":
        if self.ksch != 1:
            raise ValueError("unsupported schema version")
        if self.project is None and self.sheet is None:
            raise ValueError("document must define either project or sheet")
        if self.project is not None and self.sheet is not None:
            raise ValueError("document cannot define both project and sheet")
        self._validate_blocks()
        return self

    def _validate_blocks(self) -> None:
        assigned_refs: dict[str, str] = {}
        for block_name, block in self.blocks.items():
            for ref in block.members:
                if ref not in self.symbols:
                    raise ValueError(
                        f"blocks.{block_name}.members: ref {ref} does not exist on this sheet; "
                        f"add symbol {ref} under symbols or remove {ref} from block {block_name}"
                    )
                existing_block = assigned_refs.get(ref)
                if existing_block is not None:
                    raise ValueError(
                        f"blocks.{block_name}.members: ref {ref} is already in block "
                        f"{existing_block}; remove {ref} from one block so each symbol ref "
                        "appears in only one block"
                    )
                assigned_refs[ref] = block_name
