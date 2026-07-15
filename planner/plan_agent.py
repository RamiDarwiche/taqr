from __future__ import annotations

import pathlib

from langgraph.graph import START, MessagesState, StateGraph

from planner.nodes import (
    call_get_schema,
    check_query,
    generate_query,
    get_schema_node,
    list_tables,
    run_query_node,
    should_continue,
)

builder = StateGraph(MessagesState)
builder.add_node(list_tables)
builder.add_node(call_get_schema)
builder.add_node(get_schema_node, "get_schema")
builder.add_node(generate_query)
builder.add_node(check_query)
builder.add_node(run_query_node, "run_query")

builder.add_edge(START, "list_tables")
builder.add_edge("list_tables", "call_get_schema")
builder.add_edge("call_get_schema", "get_schema")
builder.add_edge("get_schema", "generate_query")
builder.add_conditional_edges("generate_query", should_continue)
builder.add_edge("check_query", "run_query")
builder.add_edge("run_query", "generate_query")

agent = builder.compile()


if __name__ == "__main__":
    try:
        pathlib.Path("graph.png").write_bytes(agent.get_graph().draw_mermaid_png())
        print("Wrote graph.png")
    except Exception as e:
        print(f"Skipped graph.png ({e})")
        print(agent.get_graph().draw_mermaid())

    question = "Which chip company had the largest operating income in 2025?"
    for step in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode="values",
    ):
        step["messages"][-1].pretty_print()
