"""Angular route extractor.

Angular routes are declared as TS literal arrays of {path, component, loadChildren,
loadComponent, children}. We don't have a TS AST (would need tree-sitter-typescript
which is already in the parser registry, but that adds complexity).

Pragmatic approach: regex over `path:` and the nearby `component:` /
`loadComponent:` / `loadChildren:` / `redirectTo:` fields. Tracks brace-depth
to follow nested `children: [...]` arrays and concatenate parent paths.

Limitations:
  - Doesn't resolve `loadChildren: () => import('./x').then(m => m.X)` to the
    real child routes file. Records the lazy-load target path, not the
    expanded routes.
  - Doesn't follow paths defined in a constant elsewhere.
  - Standalone provideRouter(routes) is detected the same way as Routes[] arrays.
"""
from __future__ import annotations

import re
from .quarkus_routes import Route, _join_paths

EXTENSIONS = (".ts",)

# A typical entry looks like:
#   { path: 'users', component: UsersComponent }
#   { path: 'admin', loadChildren: () => import('./admin/admin.routes').then(m => m.ADMIN_ROUTES) }
#   { path: '', loadComponent: () => import('./home').then(m => m.HomeComponent) }
#   { path: '**', redirectTo: 'home' }
_PATH_FIELD = re.compile(r'path\s*:\s*["\']([^"\']*)["\']')
_COMPONENT_FIELD = re.compile(r'component\s*:\s*(\w+)')
_LOAD_COMPONENT_FIELD = re.compile(
    r'loadComponent\s*:[^{]*?\.then\s*\(\s*\w*\s*=>\s*\w+\.(\w+)'
)
_LOAD_CHILDREN_FIELD = re.compile(
    r'loadChildren\s*:[^{]*?import\s*\(\s*["\']([^"\']+)["\']'
)
_REDIRECT_FIELD = re.compile(r'redirectTo\s*:\s*["\']([^"\']*)["\']')
_CHILDREN_OPEN = re.compile(r'children\s*:\s*\[')


def extract(source: str, file_path: str) -> list[Route]:
    if not source:
        return []
    # Skip non-routes files quickly. The signature heuristic is loose by design.
    if "Routes" not in source and "RouterModule" not in source and "provideRouter" not in source:
        return []
    if "path:" not in source and "path :" not in source:
        return []

    routes: list[Route] = []

    # Tokenize relevant lines: track an in-Routes brace-depth so children
    # can concatenate paths with their parent.
    path_stack: list[str] = []   # parent paths from outer routes
    brace_stack: list[str] = []  # what each brace opening represents: 'route' | 'children' | 'other'

    lines = source.splitlines()
    in_routes_block = False
    routes_brace_balance = 0  # tracks `[` minus `]` after a Routes-looking opener

    # Heuristic to find the start of a Routes array. We look for either:
    #   const X: Routes = [
    #   const X: Route[] = [
    #   provideRouter([
    #   RouterModule.forRoot([
    #   RouterModule.forChild([
    _ROUTES_OPENERS = re.compile(
        r'(?::\s*Routes\s*=|:\s*Route\[\]\s*=|provideRouter\s*\(|RouterModule\.for(?:Root|Child)\s*\()\s*\['
    )

    for i, line in enumerate(lines):
        if not in_routes_block:
            if _ROUTES_OPENERS.search(line):
                in_routes_block = True
                routes_brace_balance = 1  # we just opened '['
            continue

        # Update bracket balance for `[` and `]`
        opens = line.count("[")
        closes = line.count("]")
        routes_brace_balance += opens - closes
        if routes_brace_balance <= 0:
            in_routes_block = False
            path_stack = []
            continue

        # Track children: nesting via `children: [` openers
        if _CHILDREN_OPEN.search(line):
            # Push the current most-recent path onto the stack
            # (the parent's path was set by the most recent `path:` field)
            pass

        pm = _PATH_FIELD.search(line)
        if not pm:
            continue
        path = pm.group(1)
        full_path = _join_paths("/".join(path_stack), path) if path_stack else (
            "/" + path if path else "/"
        )

        # Look for the target on the same line or the next few
        target = ""
        handler_class = ""
        for j in range(i, min(i + 5, len(lines))):
            sublic = lines[j]
            cm = _COMPONENT_FIELD.search(sublic)
            if cm:
                handler_class = cm.group(1)
                target = "component"
                break
            lc = _LOAD_COMPONENT_FIELD.search(sublic)
            if lc:
                handler_class = lc.group(1)
                target = "loadComponent"
                break
            lch = _LOAD_CHILDREN_FIELD.search(sublic)
            if lch:
                handler_class = lch.group(1)
                target = "loadChildren"
                break
            rd = _REDIRECT_FIELD.search(sublic)
            if rd:
                handler_class = "→ " + rd.group(1)
                target = "redirect"
                break

        if not handler_class:
            continue

        routes.append(Route(
            framework="angular",
            http_method="",  # Angular routes don't have HTTP verbs
            path=full_path,
            handler_class=handler_class,
            handler_method=target,
            handler_symbol=f"{file_path}::{handler_class}" if target != "loadChildren"
                            else handler_class,
            file=file_path,
            line=i + 1,
        ))

    return routes
