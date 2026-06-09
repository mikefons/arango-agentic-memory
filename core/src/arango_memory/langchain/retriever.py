"""`ArangoMemoryRetriever` — modern LangChain memory injection (DESIGN.md §21).

Wraps the core `retrieve()` so any LangChain chain can pull relevant memory as
`Document`s. Embeddings are never exposed (§17). A memory fault degrades to an
empty result inside `retrieve()` itself, so a chain never breaks on memory.
"""

from __future__ import annotations

from arango.database import StandardDatabase
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from ..embedding import Embedder
from ..generation import Generator
from ..retrieve.search import retrieve


class ArangoMemoryRetriever(BaseRetriever):
    """Retrieve tenant/agent-scoped memory as ranked LangChain `Document`s."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db: StandardDatabase
    tenant_id: str
    agent_id: str
    k: int = 10
    max_memory_tokens: int = 1500
    mode: str = "lite"
    embedder: Embedder | None = None
    generator: Generator | None = None

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        result = retrieve(
            self.db,
            query=query,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            k=self.k,
            max_memory_tokens=self.max_memory_tokens,
            mode=self.mode,
            embedder=self.embedder,
            generator=self.generator,
        )
        return [
            Document(
                page_content=hit.text,
                metadata={
                    "score": hit.score,
                    "source": hit.source,
                    "tenant_id": self.tenant_id,
                    "agent_id": self.agent_id,
                },
            )
            for hit in result.hits
        ]

    def assemble_context(self, query: str) -> str:
        """Return the core's token-budgeted assembled context block for a query."""
        result = retrieve(
            self.db,
            query=query,
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            k=self.k,
            max_memory_tokens=self.max_memory_tokens,
            mode=self.mode,
            embedder=self.embedder,
            generator=self.generator,
        )
        return result.context
