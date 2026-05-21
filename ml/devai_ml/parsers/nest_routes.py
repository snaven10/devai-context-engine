"""NestJS route extractor.

Recognizes the standard Nest decorators:
  - @Controller('prefix') on the class
  - @Get(), @Post(), @Put(), @Delete(), @Patch(), @Options(), @Head() on methods
  - Argument can be empty (path = '') or a path string

Routes are emitted as:
  - http_method × (controller_prefix + method_path)
"""
from __future__ import annotations

import re
from .quarkus_routes import Route, _join_paths

EXTENSIONS = (".ts",)

_CONTROLLER_ANN = re.compile(
    r'@Controller\s*\(\s*(?:["\'`]([^"\'`]*)["\'`])?\s*\)'
)
_CLASS_DECL = re.compile(r'^\s*(?:export\s+)?class\s+(\w+)')

_HTTP_DECORATORS = ("Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All")
_METHOD_DECORATOR = re.compile(
    r'@(' + "|".join(_HTTP_DECORATORS) + r')\s*\(\s*(?:["\'`]([^"\'`]*)["\'`])?\s*\)'
)
_METHOD_DECL = re.compile(
    r'^\s*(?:public\s+|protected\s+|private\s+|async\s+)*'
    r'(\w+)\s*\([^)]*\)'                   # method name + args opening
)


def extract(source: str, file_path: str) -> list[Route]:
    if "@Controller" not in source:
        return []

    lines = source.splitlines()
    routes: list[Route] = []

    # Pass 1: locate class declaration + controller prefix
    class_name = ""
    class_path = ""
    class_decl_line = -1
    pending_controller = False
    pending_prefix = ""

    for i, line in enumerate(lines):
        cm = _CONTROLLER_ANN.search(line)
        if cm:
            pending_controller = True
            pending_prefix = cm.group(1) or ""
            continue
        cd = _CLASS_DECL.match(line)
        if cd and pending_controller:
            class_name = cd.group(1)
            class_path = pending_prefix
            class_decl_line = i
            pending_controller = False
            pending_prefix = ""
            break

    if class_decl_line < 0:
        return []

    # Pass 2: walk method decorators
    method_path = ""
    method_http = ""

    def reset():
        nonlocal method_path, method_http
        method_path = ""
        method_http = ""

    for i in range(class_decl_line + 1, len(lines)):
        line = lines[i]
        md = _METHOD_DECORATOR.search(line)
        if md:
            verb = md.group(1).upper()
            if verb == "ALL":
                verb = ""
            method_http = verb
            method_path = md.group(2) or ""
            continue

        decl = _METHOD_DECL.match(line)
        if decl and method_http is not None and method_http != "":
            method_name = decl.group(1)
            # Skip TS keywords that match the regex
            if method_name in ("if", "for", "while", "switch", "constructor", "return"):
                reset()
                continue
            full_path = _join_paths(class_path, method_path)
            routes.append(Route(
                framework="nestjs",
                http_method=method_http,
                path=full_path,
                handler_class=class_name,
                handler_method=method_name,
                handler_symbol=f"{file_path}::{class_name}.{method_name}",
                file=file_path,
                line=i + 1,
            ))
            reset()
            continue

        if line.strip() == "}":
            reset()

    return routes
