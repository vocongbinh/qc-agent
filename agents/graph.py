"""LangGraph – Phase 1+2+3+4 (API + UI + Chaos + Performance)."""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agents.state import AgentState
from agents.retriever import codeintel_retriever_node
from agents.planner import planner_node
from agents.generator import generator_node
from agents.api_executor import api_executor_node
from agents.ui_executor import ui_executor_node
from agents.chaos_executor import chaos_executor_node
from agents.performance_executor import performance_executor_node
from agents.reporter import reporter_node
from config.settings import settings


def human_review_node(state: AgentState) -> dict:
    return {
        "current_step": "waiting_human_review",
        "human_approved": state.get("human_approved", False),
    }


def should_continue_after_planner(state: AgentState) -> Literal["human_review", "generator", "end_error"]:
    if state.get("error"):
        return "end_error"
    if settings.enable_human_review and not state.get("human_approved"):
        return "human_review"
    return "generator"


def should_continue_after_generator(state: AgentState) -> Literal["api_executor", "end_error"]:
    if state.get("error") or not state.get("generated_tests"):
        return "end_error"
    return "api_executor"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("codeintel_retriever", codeintel_retriever_node)
    graph.add_node("planner", planner_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("generator", generator_node)
    graph.add_node("api_executor", api_executor_node)
    graph.add_node("ui_executor", ui_executor_node)
    graph.add_node("chaos_executor", chaos_executor_node)
    graph.add_node("performance_executor", performance_executor_node)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("codeintel_retriever")
    graph.add_edge("codeintel_retriever", "planner")

    graph.add_conditional_edges(
        "planner",
        should_continue_after_planner,
        {"human_review": "human_review", "generator": "generator", "end_error": END},
    )
    graph.add_edge("human_review", "generator")
    graph.add_conditional_edges(
        "generator",
        should_continue_after_generator,
        {"api_executor": "api_executor", "end_error": END},
    )

    graph.add_edge("api_executor", "ui_executor")
    graph.add_edge("ui_executor", "chaos_executor")
    graph.add_edge("chaos_executor", "performance_executor")
    graph.add_edge("performance_executor", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=MemorySaver())


qc_graph = build_graph()
