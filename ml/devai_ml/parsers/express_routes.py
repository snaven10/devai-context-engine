"""Express / Koa route extractor.

Recognizes:
  - app.get('/path', handler)
  - app.post('/path', handler)
  - router.put(...), router.delete(...), etc.
  - app.use('/prefix', someRouter)   (prefix only — handler tracking not chained)
  - app.METHOD(path, ...handlers, finalHandler)

The "handler" recorded is the last identifier passed to the route
registration. Inline arrow functions are recorded as 'inline'.
"""
from __future__ import annotations

import re
from .quarkus_routes import Route

EXTENSIONS = (".js", ".ts", ".mjs", ".cjs")

_VERBS = ("get", "post", "put", "delete", "patch", "head", "options", "all")

# Match expr like `app.get('/x', handler1, handler2)` or `router.post('/y', cb)`
_ROUTE_CALL = re.compile(
    r'(?P<obj>\w+)\s*\.\s*'
    r'(?P<verb>' + "|".join(_VERBS) + r')'
    r'\s*\(\s*'
    r'["\`\']([^"\`\']+)["\`\']'  # path
    r'(?P<rest>.*)'
)
# Heuristic: detect last handler name. We pick the LAST identifier-like token
# before the closing `)` on the same logical line.
_HANDLER_TOKEN = re.compile(r'([A-Za-z_$][\w$]*)\s*\)?\s*;?\s*$')


def extract(source: str, file_path: str) -> list[Route]:
    if not source:
        return []
    # Heuristic: must look like an Express/Koa file
    sl = source.lower()
    if "express" not in sl and "koa" not in sl and ".route(" not in sl and "router" not in sl:
        # Still proceed if it has obvious app.get/post patterns
        if not re.search(r'\.\s*(get|post|put|delete|patch)\s*\(\s*["\']\/', source):
            return []

    routes: list[Route] = []
    for i, line in enumerate(source.splitlines()):
        # Strip line comments before matching
        if "//" in line:
            line_clean = line.split("//", 1)[0]
        else:
            line_clean = line
        m = _ROUTE_CALL.search(line_clean)
        if not m:
            continue
        verb = m.group("verb").upper()
        if verb == "ALL":
            verb = ""  # any verb
        path = m.group(3)
        if not path.startswith("/"):
            continue  # not a real route (e.g. router.get(NOT_A_PATH, ...))
        rest = m.group("rest") or ""
        # Find handler name. Strip trailing ');' etc and look for last id.
        handler_name = ""
        tail = rest.rstrip(" );,")
        ht = _HANDLER_TOKEN.search(tail)
        if ht:
            handler_name = ht.group(1)
        if handler_name in ("", "next", "req", "res"):
            handler_name = "inline"

        routes.append(Route(
            framework="express",
            http_method=verb,
            path=path,
            handler_class="",
            handler_method=handler_name,
            handler_symbol=f"{file_path}::{handler_name}" if handler_name != "inline"
                            else f"{file_path}::<anonymous>",
            file=file_path,
            line=i + 1,
        ))
    return routes
