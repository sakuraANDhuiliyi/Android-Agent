from __future__ import annotations

import re
from typing import Any


def _location(text: str, pos: int) -> tuple[int, int]:
    """Return (line, column) for a byte position in text (1-based)."""
    before = text[:pos]
    line = before.count("\n") + 1
    column = pos - before.rfind("\n") if "\n" in before else pos + 1
    return line, column


def _strip_comments(text: str) -> str:
    """Remove line and block comments from Kotlin/Java."""
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def _extract_package(text: str) -> str | None:
    m = re.search(r"^\s*package\s+([a-zA-Z_][a-zA-Z0-9_.]*)", text, re.MULTILINE)
    return m.group(1) if m else None


def _extract_qualified_name(package: str | None, name: str) -> str:
    return f"{package}.{name}" if package else name


def _kotlin_symbols(text: str, rel_path: str) -> dict[str, Any]:
    package = _extract_package(text)
    symbols: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    clean = _strip_comments(text)

    # classes, interfaces, objects
    for m in re.finditer(
        r"\b(class|interface|object)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(|<|:|\{|$)",
        clean,
    ):
        kind, name = m.group(1), m.group(2)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": kind,
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"package": package},
            }
        )

    # functions
    for m in re.finditer(
        r"\bfun\s+((?:`[^`]+`|[a-zA-Z_][a-zA-Z0-9_]*))\s*\(([^)]*)\)",
        clean,
    ):
        name = m.group(1).strip()
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "function",
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": f"({m.group(2) or ''})",
                "extra": {"package": package},
            }
        )

    # properties / fields (simple top-level or member)
    for m in re.finditer(
        r"\b(val|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        clean,
    ):
        kind, name = "field", m.group(2)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": kind,
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"package": package},
            }
        )

    # references to R.id / R.string / R.layout
    for m in re.finditer(r"R\.(id|string|layout|drawable|color|dimen)\.(\w+)", text):
        line, column = _location(text, m.start())
        references.append(
            {
                "symbol_name": m.group(2),
                "ref_type": f"resource.{m.group(1)}",
                "line": line,
                "column": column,
            }
        )

    return {"symbols": symbols, "references": references, "lightweight": False}


def _java_symbols(text: str, rel_path: str) -> dict[str, Any]:
    package = _extract_package(text)
    symbols: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    clean = _strip_comments(text)

    for m in re.finditer(
        r"\b(class|interface|enum)\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
        clean,
    ):
        kind, name = m.group(1), m.group(2)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": kind,
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"package": package},
            }
        )

    for m in re.finditer(
        r"\b(?:public|protected|private|static|final|abstract|native|synchronized\s+)*\s*"
        r"(?:<[^>]+>\s+)?"
        r"([a-zA-Z_][a-zA-Z0-9_<>,?\s\[\]]*)\s+"
        r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)\s*(?:throws\s+[^{]+)?\s*\{",
        clean,
    ):
        return_type, name, params = m.group(1), m.group(2), m.group(3)
        if return_type in {"if", "for", "while", "switch", "catch"}:
            continue
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "function",
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": f"({params})",
                "extra": {"package": package, "return_type": return_type.strip()},
            }
        )

    for m in re.finditer(
        r"\b(?:public|protected|private|static|final|transient|volatile\s+)*\s*"
        r"([a-zA-Z_][a-zA-Z0-9_<>,?\s\[\]]*)\s+"
        r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=\s*[^;]+)?\s*;",
        clean,
    ):
        type_name, name = m.group(1), m.group(2)
        if type_name in {"return", "throw", "package", "import"}:
            continue
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "field",
                "name": name,
                "qualified_name": _extract_qualified_name(package, name),
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"package": package, "type": type_name.strip()},
            }
        )

    for m in re.finditer(r"R\.(id|string|layout|drawable|color|dimen)\.(\w+)", text):
        line, column = _location(text, m.start())
        references.append(
            {
                "symbol_name": m.group(2),
                "ref_type": f"resource.{m.group(1)}",
                "line": line,
                "column": column,
            }
        )

    return {"symbols": symbols, "references": references, "lightweight": False}


def _camel_case(name: str) -> str:
    """Convert snake_case layout name to PascalCase binding class name."""
    return "".join(part.capitalize() for part in re.split(r"[_-]", name) if part)


def _xml_symbols(text: str, rel_path: str) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []

    # String/color/dimen resources defined in values XML files.
    for m in re.finditer(r'<(string|color|dimen|bool|integer|style)\s+name="([^"]+)"', text):
        res_type, name = m.group(1), m.group(2)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "resource_id",
                "name": name,
                "qualified_name": f"R.{res_type}.{name}",
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"resource_type": res_type},
            }
        )

    # Android resource ids: android:id="@+id/foo"
    for m in re.finditer(r'android:id="@\+id/([^"]+)"', text):
        name = m.group(1)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "resource_id",
                "name": name,
                "qualified_name": f"R.id.{name}",
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"resource_type": "id"},
            }
        )

    # Resource references: @string/foo, @layout/bar, @drawable/baz
    for m in re.finditer(r'@([a-z]+)/([^"<>\s]+)', text):
        res_type, name = m.group(1), m.group(2)
        line, column = _location(text, m.start())
        references.append(
            {
                "symbol_name": name,
                "ref_type": f"resource.{res_type}",
                "line": line,
                "column": column,
            }
        )

    # Manifest components
    if "AndroidManifest.xml" in rel_path:
        for m in re.finditer(
            r'<(activity|service|receiver|provider)\s+[^>]*android:name="([^"]+)"',
            text,
            flags=re.DOTALL,
        ):
            kind, name = m.group(1), m.group(2)
            line, column = _location(text, m.start())
            symbols.append(
                {
                    "symbol_type": f"manifest.{kind}",
                    "name": name.split(".")[-1],
                    "qualified_name": name,
                    "line": line,
                    "column": column,
                    "signature": None,
                    "extra": {"manifest_component": kind},
                }
            )

    # ViewBinding: layout name maps to binding class
    if rel_path.startswith("app/src/main/res/layout/"):
        layout_name = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        binding_class = f"{_camel_case(layout_name)}Binding"
        symbols.append(
            {
                "symbol_type": "view_binding_layout",
                "name": layout_name,
                "qualified_name": binding_class,
                "line": 1,
                "column": 1,
                "signature": None,
                "extra": {"binding_class": binding_class},
            }
        )

    return {"symbols": symbols, "references": references, "lightweight": False}


def _gradle_symbols(text: str, rel_path: str) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []

    # plugins
    for m in re.finditer(r'alias\(libs\.plugins\.([a-zA-Z0-9_.-]+)\)', text):
        name = m.group(1)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "gradle_plugin",
                "name": name,
                "qualified_name": name,
                "line": line,
                "column": column,
                "signature": None,
                "extra": None,
            }
        )

    for m in re.finditer(r'id\("([^"]+)"\)', text):
        name = m.group(1)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "gradle_plugin",
                "name": name,
                "qualified_name": name,
                "line": line,
                "column": column,
                "signature": None,
                "extra": None,
            }
        )

    # dependencies: quoted coordinates and version-catalog references (libs.xxx)
    for m in re.finditer(
        r"(?:implementation|api|compileOnly|runtimeOnly|ksp|annotationProcessor|testImplementation|androidTestImplementation)\s*\(?\s*([\"'])([^\"']+)\1\s*\)?",
        text,
    ):
        coord = m.group(2)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "gradle_dependency",
                "name": coord.split(":")[1] if ":" in coord else coord,
                "qualified_name": coord,
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"coordinate": coord},
            }
        )
    for m in re.finditer(
        r"(?:implementation|api|compileOnly|runtimeOnly|ksp|annotationProcessor|testImplementation|androidTestImplementation)\s*\(?\s*(libs\.[a-zA-Z0-9_.-]+)\s*\)?",
        text,
    ):
        coord = m.group(1)
        line, column = _location(text, m.start())
        symbols.append(
            {
                "symbol_type": "gradle_dependency",
                "name": coord.split(".")[-1],
                "qualified_name": coord,
                "line": line,
                "column": column,
                "signature": None,
                "extra": {"coordinate": coord, "version_catalog": True},
            }
        )

    # module name in settings.gradle.kts: include(":app")
    if "settings.gradle" in rel_path:
        for m in re.finditer(r'include\s*\(\s*["\'](:[^"\']+)["\']\s*\)', text):
            name = m.group(1)
            line, column = _location(text, m.start())
            symbols.append(
                {
                    "symbol_type": "gradle_module",
                    "name": name,
                    "qualified_name": name,
                    "line": line,
                    "column": column,
                    "signature": None,
                    "extra": None,
                }
            )

    return {"symbols": symbols, "references": references, "lightweight": False}


def _fallback_extract(text: str, rel_path: str) -> dict[str, Any]:
    """Lightweight extraction when no specific parser is available."""
    symbols: list[dict[str, Any]] = []
    # Line count only; no symbol extraction.
    return {"symbols": symbols, "references": [], "lightweight": True}


def extract_file(text: str, rel_path: str, language: str | None) -> dict[str, Any]:
    """Extract symbols and references from a single file.

    Parsing failures are caught by the caller and recorded as an error on the
    file row; this function never raises.
    """
    suffix = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    if suffix in {"kt", "kts"}:
        # Build scripts are Gradle even if suffix is .kts.
        if rel_path.endswith("build.gradle.kts") or rel_path.endswith("settings.gradle.kts"):
            return _gradle_symbols(text, rel_path)
        return _kotlin_symbols(text, rel_path)
    if suffix == "java":
        return _java_symbols(text, rel_path)
    if suffix == "xml":
        return _xml_symbols(text, rel_path)
    if suffix in {"gradle", "kts"} or "gradle" in (language or "").lower():
        return _gradle_symbols(text, rel_path)
    return _fallback_extract(text, rel_path)
