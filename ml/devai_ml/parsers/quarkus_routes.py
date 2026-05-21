"""Regex-based extractor for Quarkus / JAX-RS REST routes.

Parses Java source files looking for `@Path`, `@GET`/`@POST`/etc annotations
and emits a list of Route records (one per (http_method, full_path, handler)).

This is intentionally regex-based, not tree-sitter — JAX-RS annotation
patterns are simple and a regex pass is ~50× faster than a full AST walk.
Edge cases (multi-line annotations with arguments, @Path on inner classes)
are handled best-effort.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

_PATH_ANNOTATION = re.compile(r'@Path\s*\(\s*"([^"]*)"\s*\)')
_HTTP_METHOD_ANNOTATION = re.compile(r'@(' + "|".join(HTTP_METHODS) + r')\b(?!\s*\()')
_CLASS_DECL = re.compile(r'^\s*(?:public\s+|abstract\s+|final\s+)*class\s+(\w+)')
_METHOD_DECL = re.compile(
    r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*'         # any preceding annotations
    r'(?:public|protected|private)\s+'         # required visibility
    r'(?:static\s+|final\s+|synchronized\s+)*'
    r'(?:[\w<>,\[\]\s\.]+?)\s+'               # return type
    r'(\w+)\s*\('                              # method name + opening paren
)


@dataclass
class Route:
    framework: str = "quarkus"
    http_method: str = ""
    path: str = ""
    handler_class: str = ""
    handler_method: str = ""
    handler_symbol: str = ""
    file: str = ""
    line: int = 0


def _join_paths(base: str, sub: str) -> str:
    """Combine class-level and method-level @Path values into a normalized URL."""
    if not base and not sub:
        return ""
    base = base.strip("/")
    sub = sub.strip("/")
    if not base:
        return "/" + sub
    if not sub:
        return "/" + base
    return "/" + base + "/" + sub


def extract_quarkus_routes(source: str, file_path: str) -> list[Route]:
    """Return one Route per (http_method, path, method) found in the source.

    Limitations:
      - Only top-level classes (one per file is the JAX-RS norm)
      - @Path with constant expressions (e.g. @Path(Routes.SOLICITUDES))
        are skipped — only string-literal paths are matched
      - Subresource locators (a @Path method that returns another resource)
        are not chained — they emit as if they were endpoints
    """
    if not source or ("@Path" not in source and "@GET" not in source and "@POST" not in source):
        return []

    lines = source.splitlines()
    routes: list[Route] = []

    # Pass 1: find class declaration and class-level @Path
    class_name = ""
    class_path = ""
    class_decl_line = -1
    pending_path = ""

    for i, line in enumerate(lines):
        m = _PATH_ANNOTATION.search(line)
        if m:
            pending_path = m.group(1)
            continue
        cm = _CLASS_DECL.match(line)
        if cm:
            class_name = cm.group(1)
            class_path = pending_path
            class_decl_line = i
            pending_path = ""
            break

    if class_decl_line < 0:
        return []

    # Pass 2: walk method by method after the class declaration. Track the
    # most recent @Path and @HTTP_METHOD encountered; when we hit a method
    # signature, emit a route.
    method_path = ""
    method_http = ""

    for i in range(class_decl_line + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue

        m = _PATH_ANNOTATION.search(line)
        if m:
            method_path = m.group(1)
            continue

        hm = _HTTP_METHOD_ANNOTATION.search(line)
        if hm:
            method_http = hm.group(1)
            continue

        # Method declaration ends the annotation block
        md = _METHOD_DECL.match(line)
        if md and (method_http or method_path):
            method_name = md.group(1)
            # Skip lifecycle/property accessors that aren't real endpoints
            if method_name in ("toString", "hashCode", "equals", "getClass"):
                method_path = ""
                method_http = ""
                continue
            full_path = _join_paths(class_path, method_path)
            r = Route(
                framework="quarkus",
                http_method=method_http or "GET",  # @Path without verb defaults to GET in some setups
                path=full_path,
                handler_class=class_name,
                handler_method=method_name,
                handler_symbol=f"{file_path}::{class_name}.{method_name}",
                file=file_path,
                line=i + 1,
            )
            # Filter clearly-not-an-endpoint (constructor, no http verb AND no @Path)
            if not r.http_method and not method_path:
                pass
            else:
                routes.append(r)
            method_path = ""
            method_http = ""
            continue

        # A `}` at column zero usually closes the class; reset state
        if stripped == "}":
            method_path = ""
            method_http = ""

    return routes
