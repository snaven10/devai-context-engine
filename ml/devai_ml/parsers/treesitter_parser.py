from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .base import Export, GraphEdge, Import, ParseResult, Symbol

logger = logging.getLogger(__name__)

# Mapping from tree-sitter node types to our normalized symbol kinds
# This is used as a fallback when query files don't provide explicit mapping
NODE_KIND_MAP: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
        "decorated_definition": "function",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type_alias",
        "enum_declaration": "enum",
        "method_definition": "method",
        "arrow_function": "function",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "arrow_function": "function",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type_alias",
        "type_spec": "struct",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "enum_declaration": "enum",
        "constructor_declaration": "method",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "impl_item": "class",
        "trait_item": "interface",
        "type_item": "type_alias",
    },
    "php": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "trait_declaration": "interface",
        "method_declaration": "method",
        "function_definition": "function",
        "enum_declaration": "enum",
        "property_declaration": "variable",
    },
}


class TreeSitterLanguageParser:
    """Generic tree-sitter based parser.

    Uses per-language .scm query files when available, falls back to
    AST walking with node type mapping for languages without queries.
    """

    # Mapping from language name to (package_name, ts_language_func)
    _GRAMMAR_PACKAGES: dict[str, str] = {
        "python": "tree_sitter_python",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_typescript",
        "go": "tree_sitter_go",
        "java": "tree_sitter_java",
        "rust": "tree_sitter_rust",
        "php": "tree_sitter_php",
    }

    def __init__(self, language: str) -> None:
        import importlib
        import tree_sitter

        self._language = language

        # Load grammar from individual tree-sitter-{lang} package
        pkg_name = self._GRAMMAR_PACKAGES.get(language)
        if pkg_name is None:
            raise ValueError(f"No tree-sitter grammar package configured for: {language}")

        try:
            mod = importlib.import_module(pkg_name)
        except ImportError:
            raise ImportError(
                f"tree-sitter grammar for {language} not installed. "
                f"Install with: pip install {pkg_name.replace('_', '-')}"
            )

        # tree-sitter 0.23+ API: Language.build() from binding
        if language == "typescript":
            # tree_sitter_typescript exposes .language_typescript() and .language_tsx()
            self._ts_lang = tree_sitter.Language(mod.language_typescript())
        elif language == "php":
            # tree_sitter_php exposes .language_php() (not .language())
            self._ts_lang = tree_sitter.Language(mod.language_php())
        else:
            self._ts_lang = tree_sitter.Language(mod.language())

        self._ts_parser = tree_sitter.Parser(self._ts_lang)
        self._queries = self._load_queries()

    @property
    def language(self) -> str:
        return self._language

    @property
    def extensions(self) -> list[str]:
        from .registry import EXTENSION_MAP
        return [ext for ext, lang in EXTENSION_MAP.items() if lang == self._language]

    def _load_queries(self) -> dict[str, Any]:
        """Load .scm query files from queries/{language}/ directory.

        Uses tree-sitter 0.25+ API: Query() constructor + QueryCursor for execution.
        """
        import tree_sitter

        queries: dict[str, Any] = {}
        query_dir = Path(__file__).parent / "queries" / self._language
        if not query_dir.exists():
            logger.debug("No query directory for %s, using AST walker fallback", self._language)
            return queries

        for scm_file in query_dir.glob("*.scm"):
            name = scm_file.stem
            query_text = scm_file.read_text().strip()
            if not query_text:
                continue
            try:
                queries[name] = tree_sitter.Query(self._ts_lang, query_text)
                logger.debug("Loaded query %s/%s.scm", self._language, name)
            except Exception as e:
                logger.warning("Failed to load query %s/%s.scm: %s", self._language, name, e)

        return queries

    def parse_file(self, file_path: str) -> ParseResult:
        """Parse a file from disk."""
        source = Path(file_path).read_bytes()
        return self._parse(source, file_path)

    def parse_source(self, source: str, file_path: str = "<string>") -> ParseResult:
        """Parse source code string."""
        return self._parse(source.encode("utf-8"), file_path)

    def _parse(self, source: bytes, file_path: str) -> ParseResult:
        """Core parsing logic.

        IMPORTANT: We pass raw bytes to all extraction methods because
        tree-sitter node.start_byte/end_byte are BYTE offsets, not character
        offsets. Using decoded strings with byte offsets causes truncated
        symbol names when the file contains multi-byte characters (ñ, ü, etc).
        """
        tree = self._ts_parser.parse(source)
        source_text = source  # keep as bytes for correct byte-offset slicing

        if "symbols" in self._queries:
            symbols = self._extract_symbols_via_query(tree, source_text, file_path)
        else:
            symbols = self._extract_symbols_via_walk(tree, source_text, file_path)

        if "imports" in self._queries:
            imports = self._extract_imports_via_query(tree, source_text, file_path)
        else:
            imports = self._extract_imports_via_walk(tree, source_text, file_path)

        if "calls" in self._queries:
            edges = self._extract_calls_via_query(tree, source_text, file_path)
        else:
            edges = self._extract_calls_via_walk(tree, source_text, file_path)

        if "exports" in self._queries:
            exports = self._extract_exports_via_query(tree, source_text, file_path)
        else:
            exports = []

        # Add import edges
        for imp in imports:
            edges.append(GraphEdge(
                source=f"{file_path}::<module>",
                target=imp.module,
                kind="imports",
                file_path=file_path,
                line=imp.line,
            ))

        return ParseResult(
            file_path=file_path,
            language=self._language,
            symbols=symbols,
            imports=imports,
            exports=exports,
            edges=edges,
            raw_tree=tree,
        )

    # ---- Query-based extraction ----

    def _extract_symbols_via_query(
        self, tree: Any, source: str, file_path: str
    ) -> list[Symbol]:
        """Extract symbols using .scm query file captures.

        Uses tree-sitter 0.25+ API: QueryCursor.matches() returns
        [(pattern_idx, {capture_name: [nodes]})].
        """
        import tree_sitter

        symbols: list[Symbol] = []
        query = self._queries["symbols"]
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        for _pattern_idx, captures_dict in matches:
            # Each match is a complete pattern match with all its captures
            data: dict[str, Any] = {}

            for capture_name, nodes in captures_dict.items():
                node = nodes[0]  # take first node per capture
                parts = capture_name.split(".")
                category = parts[0]  # function, class, method, etc.
                field = parts[1] if len(parts) > 1 else "def"

                if field == "def":
                    data["category"] = category
                    data["def_node"] = node
                    data["start_line"] = node.start_point[0] + 1
                    data["end_line"] = node.end_point[0] + 1
                    data["start_byte"] = node.start_byte
                    data["end_byte"] = node.end_byte
                elif field == "name":
                    data["name"] = self._node_text(node, source)
                    data.setdefault("category", category)
                elif field == "params":
                    data["params"] = self._node_text(node, source)
                elif field == "return_type":
                    data["return_type"] = self._node_text(node, source)
                elif field == "docstring":
                    data["docstring"] = self._node_text(node, source).strip("\"'")
                elif field == "bases":
                    data["bases"] = self._node_text(node, source)

            if data and "name" in data and "def_node" in data:
                symbols.append(self._build_symbol(data, source, file_path))

        return symbols

    def _build_symbol(self, data: dict[str, Any], source: str, file_path: str) -> Symbol:
        """Build a Symbol from captured data."""
        node = data["def_node"]
        code = self._node_text(node, source)
        name = data.get("name", "")
        category = data.get("category", "function")

        # Map category to kind
        kind_map = {"function": "function", "class": "class", "method": "method",
                     "interface": "interface", "struct": "struct", "enum": "enum",
                     "type": "type_alias", "constant": "constant", "variable": "variable",
                     "trait": "interface", "impl": "class"}
        kind = kind_map.get(category, category)

        # Build signature
        params = data.get("params", "")
        return_type = data.get("return_type")
        signature = f"{kind} {name}"
        if params:
            signature += f"({params})"
        if return_type:
            signature += f" -> {return_type}"

        # Detect visibility
        visibility = "public"
        if name.startswith("_"):
            visibility = "private" if name.startswith("__") else "protected"

        return Symbol(
            name=name,
            kind=kind,
            language=self._language,
            file_path=file_path,
            start_line=data["start_line"],
            end_line=data["end_line"],
            start_byte=data["start_byte"],
            end_byte=data["end_byte"],
            code=code,
            signature=signature,
            docstring=data.get("docstring"),
            parent=data.get("parent"),
            params=tuple(p.strip() for p in params.split(",")) if params else (),
            return_type=return_type,
            visibility=visibility,
        )

    def _extract_imports_via_query(
        self, tree: Any, source: str, file_path: str
    ) -> list[Import]:
        """Extract imports using .scm query file (tree-sitter 0.25+ API)."""
        import tree_sitter

        imports: list[Import] = []
        query = self._queries["imports"]
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        for _pattern_idx, captures_dict in matches:
            data: dict[str, Any] = {}

            for capture_name, nodes in captures_dict.items():
                parts = capture_name.split(".")
                field = parts[1] if len(parts) > 1 else parts[0]

                if field in ("def", "statement"):
                    node = nodes[0]
                    data["line"] = node.start_point[0] + 1
                elif field == "module":
                    data["module"] = self._node_text(nodes[0], source)
                elif field == "name":
                    data.setdefault("names", [])
                    for n in nodes:
                        data["names"].append(self._node_text(n, source))
                elif field == "alias":
                    data["alias"] = self._node_text(nodes[0], source)

            if "module" in data:
                imports.append(Import(
                    module=data["module"],
                    alias=data.get("alias"),
                    names=tuple(data.get("names", [])),
                    file_path=file_path,
                    line=data.get("line", 0),
                ))

        return imports

    # Per-language AST node types that act as containers we want to attribute
    # calls to. Each entry has `method` (function-like containers) and `class`
    # (type-like containers). When a call site is nested inside both, the
    # source id is `ClassName.methodName`; otherwise just whichever was found.
    _CONTAINER_NODE_TYPES: dict[str, dict[str, set[str]]] = {
        "java": {
            "method": {"method_declaration", "constructor_declaration"},
            "class":  {"class_declaration", "interface_declaration",
                       "enum_declaration", "record_declaration",
                       "annotation_type_declaration"},
        },
        "typescript": {
            "method": {"method_definition", "function_declaration",
                       "function_expression", "arrow_function",
                       "generator_function_declaration"},
            "class":  {"class_declaration", "interface_declaration",
                       "abstract_class_declaration"},
        },
        "javascript": {
            "method": {"method_definition", "function_declaration",
                       "function_expression", "arrow_function",
                       "generator_function_declaration"},
            "class":  {"class_declaration"},
        },
        "python": {
            "method": {"function_definition"},
            "class":  {"class_definition"},
        },
        "go": {
            "method": {"function_declaration", "method_declaration"},
            "class":  set(),  # Go has no class concept
        },
        "rust": {
            "method": {"function_item"},
            "class":  {"impl_item", "trait_item"},
        },
        "ruby": {
            "method": {"method", "singleton_method"},
            "class":  {"class", "module"},
        },
        "php": {
            "method": {"method_declaration", "function_definition"},
            "class":  {"class_declaration", "interface_declaration",
                       "trait_declaration"},
        },
    }

    def _find_enclosing_symbol(self, node: Any, source: bytes | str) -> str | None:
        """Walk up the AST until a method and/or class container is found.

        Returns:
            "ClassName.methodName"  when nested inside both
            "methodName"            when only a function/method container is found
            "ClassName"             when only a type container is found (rare)
            None                    when the call lives at file/module top level
        """
        types = self._CONTAINER_NODE_TYPES.get(self._language)
        if not types:
            return None
        method_types = types.get("method", set())
        class_types = types.get("class", set())

        method_name: str | None = None
        class_name: str | None = None

        cur = node.parent if hasattr(node, "parent") else None
        while cur is not None:
            t = cur.type
            if method_name is None and t in method_types:
                name = self._node_field_text(cur, "name", source)
                # Anonymous arrow/function expressions take the name of their
                # enclosing variable_declarator (TS/JS idiom `const x = () => {}`).
                if not name and t in ("arrow_function", "function_expression"):
                    parent = cur.parent
                    if parent is not None and parent.type == "variable_declarator":
                        name = self._node_field_text(parent, "name", source)
                if name:
                    method_name = name
            if class_name is None and t in class_types:
                name = self._node_field_text(cur, "name", source)
                if name:
                    class_name = name
            if method_name and class_name:
                break
            cur = cur.parent if hasattr(cur, "parent") else None

        if method_name and class_name:
            return f"{class_name}.{method_name}"
        if method_name:
            return method_name
        if class_name:
            return class_name
        return None

    @staticmethod
    def _node_field_text(node: Any, field_name: str, source: bytes | str) -> str | None:
        """Read the text of a tree-sitter field child. Returns None if missing."""
        if node is None:
            return None
        try:
            child = node.child_by_field_name(field_name)
        except Exception:
            return None
        if child is None:
            return None
        chunk = source[child.start_byte:child.end_byte]
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8", errors="replace")
        return chunk

    # ---- Target FQN resolution ------------------------------------------------
    #
    # The .scm queries capture `@call.name` as a bare identifier (`getLogger`).
    # That hides the difference between `org.slf4j.Logger.getLogger` and any
    # other `getLogger` in the codebase. Here we look at the receiver (the
    # object the method is invoked on) and the file's import declarations to
    # rebuild the fully-qualified target name when possible.
    # --------------------------------------------------------------------------

    def _build_import_map(self, tree: Any, source: bytes | str) -> dict[str, str]:
        """Return `{local_name: FQN}` for everything imported by this file."""
        if self._language == "java":
            return self._build_java_import_map(tree, source)
        if self._language in ("typescript", "javascript"):
            return self._build_ts_import_map(tree, source)
        if self._language == "python":
            return self._build_python_import_map(tree, source)
        return {}

    def _build_java_import_map(self, tree: Any, source: bytes | str) -> dict[str, str]:
        """Java: `import a.b.C;` → {'C': 'a.b.C'}. Wildcards skipped (no
        local name to anchor on). Static imports keep their member name."""
        imports: dict[str, str] = {}

        def walk(node):
            if node.type == "import_declaration":
                fqn = None
                is_wildcard = False
                for c in node.children:
                    if c.type == "scoped_identifier":
                        fqn = self._node_text(c, source)
                    elif c.type == "asterisk":
                        is_wildcard = True
                if fqn and not is_wildcard:
                    local = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
                    imports[local] = fqn
            for c in node.children:
                walk(c)

        walk(tree.root_node)
        return imports

    def _build_ts_import_map(self, tree: Any, source: bytes | str) -> dict[str, str]:
        """TS/JS: rebuild a local_name → module.Name map from the import
        statements. Module is the literal source string (path or package)."""
        imports: dict[str, str] = {}

        def text(n):
            return self._node_text(n, source)

        def harvest_clause(clause, module: str):
            for c in clause.children:
                if c.type == "identifier":
                    # `import Default from '...'`
                    imports[text(c)] = f"{module}.default"
                elif c.type == "namespace_import":
                    # `import * as ns from '...'`
                    for d in c.children:
                        if d.type == "identifier":
                            imports[text(d)] = module
                elif c.type == "named_imports":
                    # `import { A, B as C } from '...'`
                    for d in c.children:
                        if d.type == "import_specifier":
                            spec_name = None
                            spec_alias = None
                            try:
                                n = d.child_by_field_name("name")
                                a = d.child_by_field_name("alias")
                                spec_name = text(n) if n is not None else None
                                spec_alias = text(a) if a is not None else None
                            except Exception:
                                pass
                            if spec_name is None:
                                # fall back: first identifier child
                                ids = [x for x in d.children if x.type == "identifier"]
                                if ids:
                                    spec_name = text(ids[0])
                            if spec_name:
                                local = spec_alias or spec_name
                                imports[local] = f"{module}.{spec_name}"

        def walk(node):
            if node.type == "import_statement":
                module = None
                for c in node.children:
                    if c.type == "string":
                        t = text(c).strip()
                        if len(t) >= 2 and t[0] in "\"'`" and t[-1] in "\"'`":
                            module = t[1:-1]
                        else:
                            module = t
                        break
                if module is None:
                    return
                for c in node.children:
                    if c.type == "import_clause":
                        harvest_clause(c, module)
            for c in node.children:
                walk(c)

        walk(tree.root_node)
        return imports

    def _build_python_import_map(self, tree: Any, source: bytes | str) -> dict[str, str]:
        """Python: `from a.b import C [as D]` and `import a.b [as ab]`."""
        imports: dict[str, str] = {}

        def text(n):
            return self._node_text(n, source)

        def harvest_aliased(node, module_prefix: str):
            # aliased_import: name (dotted_name) + alias (identifier)
            name_n = node.child_by_field_name("name") if hasattr(node, "child_by_field_name") else None
            alias_n = node.child_by_field_name("alias") if hasattr(node, "child_by_field_name") else None
            if name_n is None:
                return
            name_text = text(name_n)
            alias_text = text(alias_n) if alias_n is not None else name_text.rsplit(".", 1)[-1]
            full = (module_prefix + "." + name_text) if module_prefix else name_text
            imports[alias_text] = full

        def walk(node):
            if node.type == "import_statement":
                for c in node.children:
                    if c.type == "dotted_name":
                        full = text(c)
                        local = full.split(".", 1)[0]  # `import foo.bar` binds `foo`
                        imports[local] = full
                    elif c.type == "aliased_import":
                        harvest_aliased(c, "")
            elif node.type == "import_from_statement":
                # First dotted_name child is the module; subsequent ones are names
                module = ""
                saw_import_kw = False
                for c in node.children:
                    if c.type in ("dotted_name", "relative_import") and not saw_import_kw:
                        module = text(c)
                    elif c.type == "import":
                        saw_import_kw = True
                    elif saw_import_kw and c.type == "dotted_name":
                        name_text = text(c)
                        local = name_text.rsplit(".", 1)[-1]
                        imports[local] = f"{module}.{name_text}" if module else name_text
                    elif saw_import_kw and c.type == "aliased_import":
                        harvest_aliased(c, module)
            for c in node.children:
                walk(c)

        walk(tree.root_node)
        return imports

    def _resolve_target_name(self, name_node: Any, source: bytes | str,
                              import_map: dict[str, str]) -> str:
        """Take an `@call.name` node and return either the bare name or its
        fully-qualified form when the receiver maps to an import."""
        call_name = self._node_text(name_node, source)
        if not call_name:
            return call_name
        parent = name_node.parent if hasattr(name_node, "parent") else None

        # Receiver-aware resolution: `Logger.getLogger` where `Logger` is imported.
        if parent is not None:
            container_type = parent.type
            obj_field = "object"
            # Java: name node lives directly under method_invocation
            # TS/JS: name node lives under member_expression which is under call_expression
            # Python: name node lives under `attribute`
            if container_type in ("method_invocation", "member_expression", "attribute"):
                try:
                    obj_node = parent.child_by_field_name(obj_field)
                except Exception:
                    obj_node = None
                if obj_node is not None and obj_node.type in ("identifier", "type_identifier"):
                    receiver = self._node_text(obj_node, source)
                    if receiver in import_map:
                        return f"{import_map[receiver]}.{call_name}"

        # Free-standing call where the function name itself is the import (no receiver)
        # — common with `from X import foo; foo()` (Python) or
        # `import static X.bar; bar()` (Java).
        if call_name in import_map:
            return import_map[call_name]

        return call_name

    def _container_source_id(self, node: Any, source: bytes | str, file_path: str) -> str:
        """Produce the source-side id for a call edge.

        Walks up from the call node to find its containing method/class.
        Falls back to `<unknown>` only when truly at file scope (e.g. top-level
        statements in Python, static initializers in Java).
        """
        enclosing = self._find_enclosing_symbol(node, source)
        if enclosing:
            return f"{file_path}::{enclosing}"
        return f"{file_path}::<unknown>"

    def _extract_calls_via_query(
        self, tree: Any, source: str, file_path: str
    ) -> list[GraphEdge]:
        """Extract function calls using .scm query file (tree-sitter 0.25+ API).

        - `source` is resolved to the symbol that physically encloses the
          call (Class.method) via _container_source_id.
        - `target` is resolved to its fully-qualified form when the receiver
          or the call name matches an import in this file
          (e.g. `Logger.getLogger` with `import org.slf4j.Logger;` becomes
          `org.slf4j.Logger.getLogger`).
        """
        import tree_sitter

        edges: list[GraphEdge] = []
        query = self._queries["calls"]
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        # One pass over imports per file — reused for every call below.
        import_map = self._build_import_map(tree, source)

        for _pattern_idx, captures_dict in matches:
            for capture_name, nodes in captures_dict.items():
                if "name" in capture_name:
                    for node in nodes:
                        edges.append(GraphEdge(
                            source=self._container_source_id(node, source, file_path),
                            target=self._resolve_target_name(node, source, import_map),
                            kind="calls",
                            file_path=file_path,
                            line=node.start_point[0] + 1,
                        ))

        return edges

    def _extract_exports_via_query(
        self, tree: Any, source: str, file_path: str
    ) -> list[Export]:
        """Extract exports using .scm query file (tree-sitter 0.25+ API)."""
        import tree_sitter

        exports: list[Export] = []
        query = self._queries["exports"]
        cursor = tree_sitter.QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        for _pattern_idx, captures_dict in matches:
            for capture_name, nodes in captures_dict.items():
                parts = capture_name.split(".")
                if "name" in parts:
                    for node in nodes:
                        exports.append(Export(
                            name=self._node_text(node, source),
                            kind=parts[0] if len(parts) > 1 else "unknown",
                            file_path=file_path,
                            line=node.start_point[0] + 1,
                        ))

        return exports

    # ---- Fallback: AST walking ----

    def _extract_symbols_via_walk(
        self, tree: Any, source: str, file_path: str
    ) -> list[Symbol]:
        """Extract symbols by walking the AST tree. Fallback when no query files exist."""
        symbols: list[Symbol] = []
        kind_map = NODE_KIND_MAP.get(self._language, {})

        def walk(node: Any, parent_name: str | None = None) -> None:
            node_type = node.type
            if node_type in kind_map:
                kind = kind_map[node_type]
                name = self._find_child_text(node, "name", source)
                if name:
                    code = self._node_text(node, source)
                    params_node = self._find_child(node, "parameters") or self._find_child(node, "formal_parameters")
                    params_text = self._node_text(params_node, source) if params_node else ""

                    symbols.append(Symbol(
                        name=name,
                        kind=kind,
                        language=self._language,
                        file_path=file_path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        code=code,
                        signature=f"{kind} {name}({params_text})" if params_text else f"{kind} {name}",
                        parent=parent_name,
                        params=tuple(p.strip() for p in params_text.split(",")) if params_text else (),
                    ))

                    # For classes, walk children with this as parent
                    if kind in ("class", "struct", "interface"):
                        for child in node.children:
                            walk(child, name)
                        return

            for child in node.children:
                walk(child, parent_name)

        walk(tree.root_node)
        return symbols

    def _extract_imports_via_walk(
        self, tree: Any, source: str, file_path: str
    ) -> list[Import]:
        """Extract imports by walking AST. Fallback."""
        imports: list[Import] = []
        import_node_types = {
            "python": ("import_statement", "import_from_statement"),
            "javascript": ("import_statement",),
            "typescript": ("import_statement",),
            "go": ("import_declaration", "import_spec"),
            "java": ("import_declaration",),
            "rust": ("use_declaration",),
            "c": ("preproc_include",),
            "cpp": ("preproc_include",),
            "php": ("namespace_use_declaration",),
        }
        types_to_check = import_node_types.get(self._language, ())

        def walk(node: Any) -> None:
            if node.type in types_to_check:
                text = self._node_text(node, source).strip()
                module = self._extract_module_from_import_text(text)
                if module:
                    imports.append(Import(
                        module=module,
                        alias=None,
                        file_path=file_path,
                        line=node.start_point[0] + 1,
                    ))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return imports

    def _extract_calls_via_walk(
        self, tree: Any, source: str, file_path: str
    ) -> list[GraphEdge]:
        """Extract function calls by walking AST. Fallback used when no
        per-language .scm query is configured. Resolves the enclosing
        container the same way as _extract_calls_via_query.
        """
        edges: list[GraphEdge] = []

        def walk(node: Any) -> None:
            if node.type == "call" or node.type == "call_expression":
                func_node = node.children[0] if node.children else None
                if func_node:
                    call_name = self._node_text(func_node, source)
                    edges.append(GraphEdge(
                        source=self._container_source_id(node, source, file_path),
                        target=call_name,
                        kind="calls",
                        file_path=file_path,
                        line=node.start_point[0] + 1,
                    ))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return edges

    # ---- Helpers ----

    @staticmethod
    def _node_text(node: Any, source: bytes | str) -> str:
        """Get the text content of a tree-sitter node.

        Handles both bytes (correct: byte offsets match) and str (legacy).
        """
        if node is None:
            return ""
        chunk = source[node.start_byte:node.end_byte]
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8", errors="replace")
        return chunk

    @staticmethod
    def _find_child(node: Any, field_name: str) -> Any | None:
        """Find a child node by field name."""
        for child in node.children:
            if child.type == field_name:
                return child
        return None

    @staticmethod
    def _find_child_text(node: Any, field_name: str, source: bytes | str) -> str | None:
        """Find a child node's text by type name (e.g. 'name' -> identifier)."""
        for child in node.children:
            if child.type == field_name or child.type == "identifier" and field_name == "name":
                chunk = source[child.start_byte:child.end_byte]
                if isinstance(chunk, bytes):
                    return chunk.decode("utf-8", errors="replace")
                return chunk
        return None

    @staticmethod
    def _extract_module_from_import_text(text: str) -> str | None:
        """Extract module name from raw import text. Simple heuristic."""
        text = text.strip().rstrip(";")
        # Python: import foo / from foo import bar
        if text.startswith("from "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1]
        if text.startswith("import "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1].rstrip(",")
        # Go: "package/path"
        if '"' in text:
            start = text.index('"') + 1
            end = text.index('"', start)
            return text[start:end]
        # PHP: use App\Models\User
        if text.startswith("use "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1].rstrip(",").rstrip(" as").split(" as ")[0]
        # C/C++: #include <file> or #include "file"
        if text.startswith("#include"):
            for delim_start, delim_end in [("<", ">"), ('"', '"')]:
                if delim_start in text:
                    start = text.index(delim_start) + 1
                    end = text.index(delim_end, start)
                    return text[start:end]
        return None
