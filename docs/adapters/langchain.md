# LangChain / LangGraph Adapter

`arango_memory.langchain` — **in-process Python** (no HTTP hop): it calls the core
functions directly. Three primitives over `store`/`retrieve`/`record_step`
(DESIGN.md §21). All projections exclude embeddings (§17).

## Install
```bash
pip install "arango-memory[langchain]"   # adds langchain-core + langgraph
```

## Primitives
```python
from arango_memory.client import ArangoMemoryClient
from arango_memory.langchain import (
    ArangoMemoryRetriever, ArangoChatMessageHistory, ArangoMemoryNode,
)

db = ArangoMemoryClient().connect()

# 1) Retriever — relevant memory as LangChain Documents (+ assemble_context()).
retriever = ArangoMemoryRetriever(db=db, tenant_id="acme", agent_id="a", k=10, mode="lite")
docs = retriever.invoke("what does the user prefer?")

# 2) Chat history — durable transcript; clear() soft-deletes (WORM-preserving).
history = ArangoChatMessageHistory(db, tenant_id="acme", agent_id="a", session_id="s1")
history.add_message(HumanMessage(content="I prefer dark mode"))
history.messages   # rebuilt from the core, in order

# 3) LangGraph nodes — recall (retrieve+inject a [MEMORY CONTEXT] SystemMessage)
#    and remember (store turns + capture tool-call/ToolMessage pairs as steps).
node = ArangoMemoryNode(db, tenant_id="acme", agent_id="a", mode="full")
graph.add_node("recall", node.recall)
graph.add_node("remember", node.remember)
```

## Notes
- The modern surface replaces the deprecated LangChain `BaseMemory`.
- `ArangoMemoryNode.recall` / `.remember` are plain callables — usable as
  `StateGraph` nodes (the module depends only on `langchain-core`; bring your own
  `StateGraph`).
- Tested against the real classes + a live `StateGraph` (keyless, fakes).
