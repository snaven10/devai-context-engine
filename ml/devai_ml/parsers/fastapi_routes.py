"""FastAPI route extractor.

Recognizes:
  - @app.get("/path"), @app.post(...), @app.put, @app.delete, @app.patch, @app.head, @app.options
  - @router.get(...) when an APIRouter is in play (prefix detection from include_router is best-effort)
  - The decorated function name becomes the handler
"""
from __future__ import annotations

import re
from .quarkus_routes import Route

EXTENSIONS = (".py",)

# Match @<obj>.<method>("/path") where method is an HTTP verb
_DECORATOR = re.compile(
    r'@(\w+)\s*\.\s*(get|post|put|delete|patch|head|options)'
    r'\s*\(\s*[fF]?["\']([^"\']+)["\']'
)
# include_router(router, prefix="/x")
_INCLUDE_ROUTER = re.compile(
    r'include_router\s*\(\s*\w+\s*(?:,\s*prefix\s*=\s*["\']([^"\']+)["\'])?'
)
# APIRouter(prefix="/x")
_APIROUTER_PREFIX = re.compile(r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)["\']')
# Function def after decorator
_FUNC_DEF = re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(')


def extract(source: str, file_path: str) -> list[Route]:
    if not source or "@" not in source:
        return []
    # Heuristic: not a FastAPI file unless it imports fastapi
    if "fastapi" not in source and "FastAPI" not in source and "APIRouter" not in source:
        return []

    lines = source.splitlines()

    # APIRouter prefix declared in the file (first found wins)
    router_prefix = ""
    for line in lines:
        m = _APIROUTER_PREFIX.search(line)
        if m:
            router_prefix = m.group(1).rstrip("/")
            break

    routes: list[Route] = []
    pending: list[dict] = []  # decorators waiting for the function def

    for i, line in enumerate(lines):
        m = _DECORATOR.search(line)
        if m:
            pending.append({
                "obj": m.group(1),
                "method": m.group(2).upper(),
                "path": m.group(3),
                "line": i + 1,
            })
            continue

        fd = _FUNC_DEF.match(line)
        if fd and pending:
            func_name = fd.group(1)
            for p in pending:
                full_path = p["path"]
                if p["obj"] != "app" and router_prefix:
                    full_path = router_prefix.rstrip("/") + "/" + p["path"].lstrip("/")
                if not full_path.startswith("/"):
                    full_path = "/" + full_path
                routes.append(Route(
                    framework="fastapi",
                    http_method=p["method"],
                    path=full_path,
                    handler_class="",
                    handler_method=func_name,
                    handler_symbol=f"{file_path}::{func_name}",
                    file=file_path,
                    line=p["line"],
                ))
            pending = []
            continue

        # If we hit a non-decorator non-def line, drop pending stack
        if line.strip() and not line.strip().startswith("@") and not line.strip().startswith("#"):
            if pending and not _FUNC_DEF.match(line):
                # Allow blank/comment between but reset on real code
                pass

    return routes
