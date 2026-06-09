"""LangChain / LangGraph adapter — in-process over the core (DESIGN.md §21).

Exercises the real adapter classes against testcontainers ArangoDB with the
default Fake providers (keyless/deterministic), plus a live LangGraph StateGraph.
"""

from __future__ import annotations

from collections.abc import Callable

from arango.database import StandardDatabase
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from arango_memory.ingest.procedural import get_steps
from arango_memory.langchain import (
    ArangoChatMessageHistory,
    ArangoMemoryNode,
    ArangoMemoryRetriever,
)
from arango_memory.retrieve.search import RetrieveResult


# ── retriever ─────────────────────────────────────────────
def test_retriever_returns_documents_without_embeddings(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    history = ArangoChatMessageHistory(db, tenant_id="lc1", agent_id="a", session_id="s")
    history.add_message(HumanMessage(content="Alice loves espresso"))
    wait_for_searchable(db, query="espresso", tenant_id="lc1", agent_id="a")

    retriever = ArangoMemoryRetriever(db=db, tenant_id="lc1", agent_id="a")
    docs = retriever.invoke("espresso")
    assert any("espresso" in d.page_content for d in docs)
    assert all("embedding" not in d.metadata for d in docs)
    assert all(d.metadata["source"] for d in docs)


def test_retriever_assemble_context(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ArangoChatMessageHistory(db, tenant_id="lc2", agent_id="a", session_id="s").add_message(
        HumanMessage(content="The capital of France is Paris")
    )
    wait_for_searchable(db, query="France", tenant_id="lc2", agent_id="a")
    ctx = ArangoMemoryRetriever(db=db, tenant_id="lc2", agent_id="a").assemble_context("France")
    assert "Paris" in ctx


# ── chat message history ──────────────────────────────────
def test_history_roundtrip_preserves_roles_and_order(db: StandardDatabase) -> None:
    history = ArangoChatMessageHistory(db, tenant_id="lc3", agent_id="a", session_id="s1")
    history.add_messages(
        [HumanMessage(content="hello"), AIMessage(content="hi there"),
         HumanMessage(content="bye")]
    )
    loaded = ArangoChatMessageHistory(db, tenant_id="lc3", agent_id="a", session_id="s1").messages
    assert [(type(m).__name__, m.content) for m in loaded] == [
        ("HumanMessage", "hello"),
        ("AIMessage", "hi there"),
        ("HumanMessage", "bye"),
    ]


def test_history_is_session_scoped(db: StandardDatabase) -> None:
    ArangoChatMessageHistory(db, tenant_id="lc4", agent_id="a", session_id="s1").add_message(
        HumanMessage(content="in session one")
    )
    other = ArangoChatMessageHistory(db, tenant_id="lc4", agent_id="a", session_id="s2")
    assert other.messages == []


def test_history_clear_soft_deletes(db: StandardDatabase) -> None:
    history = ArangoChatMessageHistory(db, tenant_id="lc5", agent_id="a", session_id="s1")
    history.add_message(HumanMessage(content="forget me"))
    assert len(history.messages) == 1
    history.clear()
    reopened = ArangoChatMessageHistory(db, tenant_id="lc5", agent_id="a", session_id="s1")
    assert reopened.messages == []
    # WORM: the episode is tombstoned (invalid_at set), not hard-deleted.
    remaining = list(
        db.aql.execute(
            "FOR e IN episodes FILTER e.tenant_id == 'lc5' RETURN e.invalid_at",
        )
    )
    assert remaining and all(v is not None for v in remaining)


# ── LangGraph node ────────────────────────────────────────
def test_memory_node_recall_injects_context(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ArangoChatMessageHistory(db, tenant_id="lc6", agent_id="a", session_id="s").add_message(
        HumanMessage(content="Bob prefers tea over coffee")
    )
    wait_for_searchable(db, query="tea", tenant_id="lc6", agent_id="a")

    node = ArangoMemoryNode(db, tenant_id="lc6", agent_id="a")
    update = node.recall({"messages": [HumanMessage(content="what does Bob like?")]})
    block = update["messages"][0]
    assert isinstance(block, SystemMessage)
    assert "[MEMORY CONTEXT]" in block.content and "tea" in block.content


def test_memory_node_remember_stores_and_captures_tools(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    node = ArangoMemoryNode(db, tenant_id="lc7", agent_id="a", session_id="s")
    node.remember(
        {
            "messages": [
                HumanMessage(content="what's the weather in Paris?"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "weather", "args": {"city": "Paris"}, "id": "t1"}],
                ),
                ToolMessage(content="sunny, 22C", tool_call_id="t1"),
                AIMessage(content="It's sunny and 22C in Paris."),
            ]
        }
    )
    result = wait_for_searchable(db, query="weather Paris", tenant_id="lc7", agent_id="a")
    assert result.hits

    steps = get_steps(db, tenant_id="lc7", agent_id="a")
    assert any(s["tool_name"] == "weather" and s["outcome"] == "success" for s in steps)


def test_memory_node_dedupes_tool_capture(db: StandardDatabase) -> None:
    node = ArangoMemoryNode(db, tenant_id="lc8", agent_id="a")
    messages = [
        AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "x1"}]),
        ToolMessage(content="ok", tool_call_id="x1"),
    ]
    node._capture_tools(messages)
    node._capture_tools(messages)  # re-seen → no double count
    steps = get_steps(db, tenant_id="lc8", agent_id="a")
    search = next(s for s in steps if s["tool_name"] == "search")
    assert search["use_count"] == 1


def test_memory_node_captures_tool_failure(db: StandardDatabase) -> None:
    node = ArangoMemoryNode(db, tenant_id="lc9", agent_id="a")
    node._capture_tools(
        [
            AIMessage(content="", tool_calls=[{"name": "fetch", "args": {}, "id": "f1"}]),
            ToolMessage(content="boom", tool_call_id="f1", status="error"),
        ]
    )
    steps = get_steps(db, tenant_id="lc9", agent_id="a")
    assert any(s["tool_name"] == "fetch" and s["outcome"] == "failure" for s in steps)


# ── end-to-end via a real StateGraph ──────────────────────
def test_langgraph_state_graph_integration(
    db: StandardDatabase, wait_for_searchable: Callable[..., RetrieveResult]
) -> None:
    ArangoChatMessageHistory(db, tenant_id="lc10", agent_id="a", session_id="s").add_message(
        HumanMessage(content="The launch code is alpha-seven")
    )
    wait_for_searchable(db, query="launch code", tenant_id="lc10", agent_id="a")
    node = ArangoMemoryNode(db, tenant_id="lc10", agent_id="a")

    def agent(state: MessagesState) -> dict[str, object]:
        # the memory block injected by recall is visible to the model here
        return {"messages": [AIMessage(content="responded")]}

    graph = StateGraph(MessagesState)
    graph.add_node("recall", node.recall)
    graph.add_node("agent", agent)
    graph.add_edge(START, "recall")
    graph.add_edge("recall", "agent")
    graph.add_edge("agent", END)
    app = graph.compile()

    out = app.invoke({"messages": [HumanMessage(content="what is the launch code?")]})
    texts = [m.content for m in out["messages"] if isinstance(m, SystemMessage)]
    assert any("alpha-seven" in t for t in texts)
