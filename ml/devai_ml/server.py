from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .chunking.semantic_chunker import SemanticChunker
from .embeddings.factory import create_provider
from .embeddings.base import EmbeddingProvider
from .parsers.registry import ParserRegistry
from .pipeline.orchestrator import IndexPipeline
from .stores.graph_store import SQLiteGraphStore
from .stores.index_state import IndexStateStore
from .stores.vector_store import LanceDBVectorStore

logger = logging.getLogger(__name__)


class MLService:
    """DevAI ML Service — handles embedding, parsing, and indexing.

    Communicates via JSON-RPC over stdio. Will be upgraded to gRPC
    once proto stubs are generated.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._config = config

        # Initialize components
        logger.info("Initializing ML service...")
        self._embedding = create_provider(config.get("embeddings", {}))
        self._parser_registry = ParserRegistry()
        self._chunker = SemanticChunker()

        # Storage paths
        state_dir = Path(config.get("state_dir", ".devai/state"))
        state_dir.mkdir(parents=True, exist_ok=True)

        self._vector_store = LanceDBVectorStore(
            db_path=str(state_dir / "vectors"),
            dimension=self._embedding.dimension(),
        )
        self._graph_store = SQLiteGraphStore(
            db_path=str(state_dir / "index.db"),
        )
        self._index_store = IndexStateStore(
            db_path=str(state_dir / "index.db"),
        )

        self._pipeline = IndexPipeline(
            parser_registry=self._parser_registry,
            chunker=self._chunker,
            embedding=self._embedding,
            vector_store=self._vector_store,
            graph_store=self._graph_store,
            index_store=self._index_store,
        )

        logger.info(
            "ML service ready (model=%s, dim=%d, languages=%d)",
            self._embedding.model_name(),
            self._embedding.dimension(),
            len(self._parser_registry.supported_languages()),
        )

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a JSON-RPC request."""
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        try:
            result = self._dispatch(method, params)
            return {"jsonrpc": "2.0", "result": result, "id": req_id}
        except Exception as e:
            logger.error("Error handling %s: %s", method, e, exc_info=True)
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": req_id,
            }

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Route method to handler."""
        handlers = {
            "embed": self._handle_embed,
            "embed_batch": self._handle_embed_batch,
            "parse_file": self._handle_parse_file,
            "index_repo": self._handle_index_repo,
            "health": self._handle_health,
        }

        handler = handlers.get(method)
        if handler is None:
            raise ValueError(f"Unknown method: {method}")
        return handler(params)

    def _handle_embed(self, params: dict) -> dict:
        """Generate embedding for a single text."""
        text = params["text"]
        vector = self._embedding.embed_single(text)
        return {
            "vector": vector,
            "dimension": self._embedding.dimension(),
            "model": self._embedding.model_name(),
        }

    def _handle_embed_batch(self, params: dict) -> dict:
        """Generate embeddings for multiple texts."""
        texts = params["texts"]
        vectors = self._embedding.embed(texts)
        return {
            "vectors": vectors,
            "dimension": self._embedding.dimension(),
            "model": self._embedding.model_name(),
            "count": len(vectors),
        }

    def _handle_parse_file(self, params: dict) -> dict:
        """Parse a file and return symbols, imports, edges."""
        file_path = params.get("file_path", "")
        content = params.get("content")
        language = params.get("language")

        if content:
            parser = self._parser_registry.get_parser(file_path)
            if parser is None:
                return {"error": f"No parser for {file_path}", "symbols": [], "imports": [], "edges": []}
            result = parser.parse_source(content, file_path)
        else:
            parser = self._parser_registry.get_parser(file_path)
            if parser is None:
                return {"error": f"No parser for {file_path}", "symbols": [], "imports": [], "edges": []}
            result = parser.parse_file(file_path)

        return {
            "file_path": result.file_path,
            "language": result.language,
            "symbols": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "signature": s.signature,
                    "parent": s.parent,
                    "visibility": s.visibility,
                }
                for s in result.symbols
            ],
            "imports": [
                {"module": i.module, "alias": i.alias, "names": list(i.names), "line": i.line}
                for i in result.imports
            ],
            "edges": [
                {"source": e.source, "target": e.target, "kind": e.kind, "line": e.line}
                for e in result.edges
            ],
        }

    def _handle_index_repo(self, params: dict) -> dict:
        """Index a repository."""
        repo_path = params["repo_path"]
        branch = params.get("branch")
        incremental = params.get("incremental", True)

        result = self._pipeline.index_repo(repo_path, branch, incremental)
        return {
            "repo_path": result.repo_path,
            "branch": result.branch,
            "commit": result.commit,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "chunks_created": result.chunks_created,
            "symbols_found": result.symbols_found,
            "edges_found": result.edges_found,
            "duration_seconds": round(result.duration_seconds, 2),
            "errors": result.errors,
        }

    def _handle_health(self, params: dict) -> dict:
        """Health check."""
        return {
            "status": "serving",
            "model_loaded": self._embedding.model_name(),
            "model_dimension": self._embedding.dimension(),
            "languages_supported": self._parser_registry.supported_languages(),
        }


def serve_stdio(config: dict[str, Any] | None = None) -> None:
    """Run the ML service over stdin/stdout JSON-RPC."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # logs go to stderr, JSON-RPC goes to stdout
    )

    service = MLService(config)

    # Signal ready
    sys.stderr.write("DEVAI_ML_READY\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = service.handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError as e:
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()


def main() -> None:
    """Entry point for the devai-ml command."""
    import argparse

    parser = argparse.ArgumentParser(description="DevAI ML Service")
    parser.add_argument("--config", type=str, help="Path to config YAML file")
    parser.add_argument("--state-dir", type=str, default=".devai/state", help="State directory")
    parser.add_argument("--model", type=str, default="minilm-l6", help="Embedding model key")
    parser.add_argument("--device", type=str, default="cpu", help="Device (cpu/cuda)")
    args = parser.parse_args()

    config = {
        "state_dir": args.state_dir,
        "embeddings": {
            "provider": "local",
            "model": args.model,
            "device": args.device,
        },
    }

    if args.config:
        import yaml
        with open(args.config) as f:
            file_config = yaml.safe_load(f)
            if file_config:
                config.update(file_config)

    serve_stdio(config)


if __name__ == "__main__":
    main()
