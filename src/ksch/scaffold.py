import re
import shutil
import stat
from pathlib import Path

from ksch.errors import KschError
from ksch.importer import ImportedProject, import_project

PROJECT_TEMPLATE = """\
ksch: 1
project:
  name: {project_name}
  title: {title}
libraries:
  symbols:
    project:
      Starter: lib/Starter.kicad_sym
symbols:
  J1:
    lib: Starter:Conn4
    value: USB_IN
    connects:
      VBUS: VBUS
      GND: GND
      D+: nc
      D-: nc
  U1:
    lib: Starter:LDO_3Pin
    value: 3V3_REG
    connects:
      VIN: VBUS
      VOUT: +3V3
      GND: GND
  C1:
    lib: Starter:C
    value: 10uF
    connects:
      '1': VBUS
      '2': GND
  C2:
    lib: Starter:C
    value: 100nF
    connects:
      '1': +3V3
      '2': GND
"""


STARTER_SYMBOL_LIBRARY = """\
(kicad_symbol_lib
  (version 20240101)
  (generator "ksch")
  (symbol "Conn4"
    (property "Reference" "J" (at 0 6.35 0))
    (property "Value" "Conn4" (at 0 3.81 0))
    (symbol "Conn4_1_1"
      (rectangle
        (start -2.54 2.54)
        (end 2.54 -10.16)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
      (pin passive line (at 5.08 0 180) (length 2.54) (name "VBUS") (number "1"))
      (pin bidirectional line (at 5.08 -2.54 180) (length 2.54) (name "D+") (number "2"))
      (pin bidirectional line (at 5.08 -5.08 180) (length 2.54) (name "D-") (number "3"))
      (pin passive line (at 5.08 -7.62 180) (length 2.54) (name "GND") (number "4"))
    )
  )
  (symbol "LDO_3Pin"
    (property "Reference" "U" (at -7.62 7.62 0))
    (property "Value" "LDO_3Pin" (at -7.62 5.08 0))
    (symbol "LDO_3Pin_1_1"
      (rectangle
        (start -5.08 3.81)
        (end 5.08 -3.81)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
      (pin passive line (at -7.62 0 0) (length 2.54) (name "VIN") (number "1"))
      (pin passive line (at 7.62 0 180) (length 2.54) (name "VOUT") (number "2"))
      (pin passive line (at 0 -6.35 90) (length 2.54) (name "GND") (number "3"))
    )
  )
  (symbol "C"
    (property "Reference" "C" (at 0 5.08 0))
    (property "Value" "C" (at 0 -5.08 0))
    (symbol "C_1_1"
      (rectangle
        (start -1.27 1.27)
        (end 1.27 -1.27)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
      (pin passive line (at 0 3.81 270) (length 2.54) (name "1") (number "1"))
      (pin passive line (at 0 -3.81 90) (length 2.54) (name "2") (number "2"))
    )
  )
)
"""


GEN_SCRIPT_TEMPLATE = """\
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KSCH_BIN="${KSCH_BIN:-ksch}"

if ! command -v "$KSCH_BIN" >/dev/null 2>&1; then
  echo "ksch executable not found. Install kicad-schema or set KSCH_BIN=/path/to/ksch." >&2
  exit 127
fi

"$KSCH_BIN" gen --config "$ROOT/ksch.toml"
"""


README_TEMPLATE = """\
# {title}

This project uses `ksch` as the text source of truth for its KiCad schematic.

Generate the KiCad project:

```sh
ksch gen
```

Open `kicad/{project_name}.kicad_pro` in KiCad after generation.

The source schema is `schematic/project.ksch.yaml`. The generated KiCad files in
`kicad/` should be regenerated from the schema instead of edited by hand. The
shell script in `scripts/` is a thin wrapper around `ksch gen`.
"""


GITIGNORE_TEMPLATE = """\
.DS_Store
*.bak
*-backups/
"""


KSCH_CONFIG_TEMPLATE = """\
schema = "{schema_path}"
out = "{out_path}"
"""


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return slug or "ksch-project"


def create_starter_project(
    target: Path,
    *,
    project_name: str | None = None,
    force: bool = False,
) -> None:
    name = _slug(project_name or target.name)
    title = name.replace("-", " ").replace("_", " ").title()

    if target.exists() and any(target.iterdir()) and not force:
        raise KschError(f"{target} already exists and is not empty")

    schematic_dir = target / "schematic"
    library_dir = schematic_dir / "lib"
    scripts_dir = target / "scripts"
    kicad_dir = target / "kicad"
    library_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    kicad_dir.mkdir(parents=True, exist_ok=True)

    (schematic_dir / "project.ksch.yaml").write_text(
        PROJECT_TEMPLATE.format(project_name=name, title=title),
        encoding="utf-8",
    )
    (library_dir / "Starter.kicad_sym").write_text(STARTER_SYMBOL_LIBRARY, encoding="utf-8")
    script_path = scripts_dir / "gen-schematic.sh"
    script_path.write_text(GEN_SCRIPT_TEMPLATE, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (target / "ksch.toml").write_text(
        KSCH_CONFIG_TEMPLATE.format(
            schema_path="schematic/project.ksch.yaml",
            out_path="kicad",
        ),
        encoding="utf-8",
    )
    (target / "README.md").write_text(
        README_TEMPLATE.format(project_name=name, title=title),
        encoding="utf-8",
    )
    (target / ".gitignore").write_text(GITIGNORE_TEMPLATE, encoding="utf-8")


def create_project_from_kicad(
    target: Path,
    *,
    root_schematic: Path,
    force: bool = False,
) -> ImportedProject:
    if not target.exists():
        raise KschError(f"{target} does not exist")
    if not target.is_dir():
        raise KschError(f"{target} is not a directory")

    resolved_root = resolve_kicad_root(root_schematic, base_dir=target)
    schema_dir = target / "ksch"
    script_path = target / "scripts" / "gen-ksch-schematic.sh"
    config_path = target / "ksch.toml"
    out_path = _relative_output_path(target, resolved_root.parent)

    _refuse_existing_path(schema_dir, force=force)
    _refuse_existing_path(script_path, force=force)
    _refuse_existing_path(config_path, force=force)
    if force and schema_dir.exists():
        shutil.rmtree(schema_dir)

    imported = import_project(resolved_root, schema_dir)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(GEN_SCRIPT_TEMPLATE, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    config_path.write_text(
        KSCH_CONFIG_TEMPLATE.format(
            schema_path="ksch/project.ksch.yaml",
            out_path=out_path,
        ),
        encoding="utf-8",
    )
    return imported


def discover_kicad_roots(target: Path) -> list[Path]:
    if not target.exists():
        return []
    if target.is_file():
        return [target.resolve()] if target.suffix == ".kicad_sch" else []

    root_candidates = _kicad_roots_in_dir(target)
    if root_candidates:
        return [candidate.resolve() for candidate in root_candidates]

    search_dirs = [child for child in sorted(target.iterdir()) if child.is_dir()]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in search_dirs:
        for schematic in _kicad_roots_in_dir(directory):
            resolved = schematic.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def resolve_kicad_root(path: Path, *, base_dir: Path | None = None) -> Path:
    candidate = path
    if not candidate.is_absolute() and base_dir is not None and not candidate.exists():
        candidate = base_dir / candidate
    candidate = candidate.resolve()
    if candidate.is_file():
        if candidate.suffix != ".kicad_sch":
            raise KschError(f"{candidate} is not a .kicad_sch file")
        return candidate
    if not candidate.is_dir():
        raise KschError(f"{candidate} does not exist")

    project_files = sorted(candidate.glob("*.kicad_pro"))
    for project_file in project_files:
        schematic = project_file.with_suffix(".kicad_sch")
        if schematic.exists():
            return schematic.resolve()

    schematics = sorted(candidate.glob("*.kicad_sch"))
    if len(schematics) == 1:
        return schematics[0].resolve()
    if not schematics:
        raise KschError(f"{candidate} does not contain a .kicad_sch file")
    raise KschError(
        f"{candidate} contains multiple .kicad_sch files; run ksch init from the KiCad project dir"
    )


def _kicad_roots_in_dir(directory: Path) -> list[Path]:
    project_schematics: list[Path] = []
    for project_file in sorted(directory.glob("*.kicad_pro")):
        schematic = project_file.with_suffix(".kicad_sch")
        if schematic.exists():
            project_schematics.append(schematic)
    if project_schematics:
        return project_schematics
    return sorted(directory.glob("*.kicad_sch"))


def _relative_output_path(target: Path, output_dir: Path) -> str:
    try:
        relative = output_dir.resolve().relative_to(target.resolve())
    except ValueError:
        return str(output_dir.resolve())
    text = relative.as_posix()
    return text or "."


def _refuse_existing_path(path: Path, *, force: bool) -> None:
    if not path.exists() or force:
        return
    if path.is_dir():
        raise KschError(f"{path} already exists; pass --force to replace it")
    raise KschError(f"{path} already exists; pass --force to overwrite it")
