"""Spring MVC / Spring Boot REST route extractor.

Recognizes:
  - @RequestMapping("/x", method = RequestMethod.GET)
  - @GetMapping, @PostMapping, @PutMapping, @DeleteMapping, @PatchMapping
  - @Controller / @RestController on the class
  - Class-level @RequestMapping prefix combined with method-level paths

Regex-based, like quarkus_routes.py.
"""
from __future__ import annotations

import re
from .quarkus_routes import Route, _join_paths

EXTENSIONS = (".java", ".kt")

# Path matchers
_REQUEST_MAPPING = re.compile(
    r'@RequestMapping\s*\(\s*(?:'
    r'value\s*=\s*)?'
    r'"([^"]*)"'
)
_GET_MAPPING    = re.compile(r'@GetMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')
_POST_MAPPING   = re.compile(r'@PostMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')
_PUT_MAPPING    = re.compile(r'@PutMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')
_DELETE_MAPPING = re.compile(r'@DeleteMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')
_PATCH_MAPPING  = re.compile(r'@PatchMapping\s*\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)')

# Bare versions: @GetMapping (no args) → path inherits class path
_BARE_GET    = re.compile(r'@GetMapping\b(?!\s*\()')
_BARE_POST   = re.compile(r'@PostMapping\b(?!\s*\()')
_BARE_PUT    = re.compile(r'@PutMapping\b(?!\s*\()')
_BARE_DELETE = re.compile(r'@DeleteMapping\b(?!\s*\()')
_BARE_PATCH  = re.compile(r'@PatchMapping\b(?!\s*\()')

# Controllers
_CLASS_DECL = re.compile(
    r'^\s*(?:public\s+|abstract\s+|final\s+)*'
    r'class\s+(\w+)'
)
_CONTROLLER_ANN = re.compile(r'@(?:Rest)?Controller\b')

# Method signature (Java/Kotlin friendly enough for typical cases)
_METHOD_DECL = re.compile(
    r'^\s*(?:@\w+(?:\([^)]*\))?\s*)*'
    r'(?:public|protected|private|fun)\s+'
    r'(?:static\s+|final\s+|suspend\s+)*'
    r'(?:[\w<>,\[\]\s\.\?]+?)\s+'
    r'(\w+)\s*\('
)

# Method extraction from @RequestMapping(... method = RequestMethod.GET ...)
_METHOD_IN_REQMAP = re.compile(r'method\s*=\s*RequestMethod\.(\w+)')


def extract(source: str, file_path: str) -> list[Route]:
    if not source or "Mapping" not in source:
        return []
    if "@Controller" not in source and "@RestController" not in source:
        # Plain @Component / @Service files aren't endpoints
        return []

    lines = source.splitlines()
    routes: list[Route] = []

    # Pass 1: find class + class-level @RequestMapping
    class_name = ""
    class_path = ""
    class_decl_line = -1
    pending_path = ""
    saw_controller = False

    for i, line in enumerate(lines):
        if _CONTROLLER_ANN.search(line):
            saw_controller = True
        rm = _REQUEST_MAPPING.search(line)
        if rm and class_decl_line < 0:
            pending_path = rm.group(1)
            continue
        cm = _CLASS_DECL.match(line)
        if cm and saw_controller:
            class_name = cm.group(1)
            class_path = pending_path
            class_decl_line = i
            pending_path = ""
            break
        elif cm and not saw_controller:
            return []  # not a controller, abort

    if class_decl_line < 0:
        return []

    # Pass 2: walk methods accumulating annotations
    method_path = ""
    method_http = ""

    def reset():
        nonlocal method_path, method_http
        method_path = ""
        method_http = ""

    for i in range(class_decl_line + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            continue

        # @GetMapping("/x") etc
        for pat, http in (
            (_GET_MAPPING, "GET"), (_POST_MAPPING, "POST"),
            (_PUT_MAPPING, "PUT"), (_DELETE_MAPPING, "DELETE"),
            (_PATCH_MAPPING, "PATCH"),
        ):
            m = pat.search(line)
            if m:
                method_path = m.group(1)
                method_http = http
                break

        # Bare @GetMapping (no path)
        for pat, http in (
            (_BARE_GET, "GET"), (_BARE_POST, "POST"),
            (_BARE_PUT, "PUT"), (_BARE_DELETE, "DELETE"),
            (_BARE_PATCH, "PATCH"),
        ):
            if pat.search(line):
                method_http = http
                break

        # Verbose @RequestMapping("/x", method = RequestMethod.POST)
        rm = _REQUEST_MAPPING.search(line)
        if rm:
            method_path = rm.group(1)
            mm = _METHOD_IN_REQMAP.search(line)
            if mm:
                method_http = mm.group(1).upper()

        # Method declaration
        md = _METHOD_DECL.match(line)
        if md and method_http:
            method_name = md.group(1)
            if method_name in ("toString", "hashCode", "equals", "getClass"):
                reset()
                continue
            full_path = _join_paths(class_path, method_path)
            routes.append(Route(
                framework="spring",
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

        if stripped == "}":
            reset()

    return routes
