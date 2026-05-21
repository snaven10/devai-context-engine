"""Flask route extractor.

Recognizes:
  - @app.route('/path', methods=['GET', 'POST'])
  - @blueprint.route('/path')
  - @bp.route(...)
  - Default method is GET if `methods` is missing

Returns one Route per (method, path) pair.
"""
from __future__ import annotations

import re
from .quarkus_routes import Route

EXTENSIONS = (".py",)

# @<obj>.route("/path", methods=["GET", "POST"])  or just @<obj>.route("/x")
_ROUTE_DECORATOR = re.compile(
    r'@(\w+)\s*\.\s*route\s*\(\s*'
    r'["\']([^"\']+)["\']'                 # path
    r'(?:[^)]*methods\s*=\s*\[([^\]]+)\])?'  # optional methods=[...]
)
# Blueprint(__name__, url_prefix="/x") declaration
_BLUEPRINT_PREFIX = re.compile(
    r'Blueprint\s*\([^)]*url_prefix\s*=\s*["\']([^"\']+)["\']'
)
_FUNC_DEF = re.compile(r'^\s*def\s+(\w+)\s*\(')


def _parse_methods(methods_str: str | None) -> list[str]:
    if not methods_str:
        return ["GET"]
    # methods=['GET', 'POST']
    out = []
    for tok in re.findall(r'["\'](\w+)["\']', methods_str):
        out.append(tok.upper())
    return out or ["GET"]


def extract(source: str, file_path: str) -> list[Route]:
    if not source or ".route(" not in source:
        return []
    # Skip files that aren't Flask
    if "flask" not in source.lower() and "Flask" not in source and "Blueprint" not in source:
        return []

    lines = source.splitlines()

    # First-pass: blueprint prefix if present (first wins)
    bp_prefix = ""
    for line in lines:
        m = _BLUEPRINT_PREFIX.search(line)
        if m:
            bp_prefix = m.group(1).rstrip("/")
            break

    routes: list[Route] = []
    pending: list[dict] = []

    for i, line in enumerate(lines):
        m = _ROUTE_DECORATOR.search(line)
        if m:
            pending.append({
                "obj": m.group(1),
                "path": m.group(2),
                "methods": _parse_methods(m.group(3)),
                "line": i + 1,
            })
            continue

        fd = _FUNC_DEF.match(line)
        if fd and pending:
            func_name = fd.group(1)
            for p in pending:
                # Apply blueprint prefix if the decorator object isn't `app`
                full_path = p["path"]
                if p["obj"] != "app" and bp_prefix:
                    full_path = bp_prefix + "/" + p["path"].lstrip("/")
                if not full_path.startswith("/"):
                    full_path = "/" + full_path
                for verb in p["methods"]:
                    routes.append(Route(
                        framework="flask",
                        http_method=verb,
                        path=full_path,
                        handler_class="",
                        handler_method=func_name,
                        handler_symbol=f"{file_path}::{func_name}",
                        file=file_path,
                        line=p["line"],
                    ))
            pending = []

    return routes
