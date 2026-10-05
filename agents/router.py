"""Intent Router — lớp điều phối OpenCode-style (LLM chọn ý định, KHÔNG keyword).

LLM đọc câu của user + mô tả các năng lực, tự quyết định một trong các intent:
- answer: trò chuyện / hỏi đáp / giải thích (không chạy test, không sinh plan)
- plan:   lập Test Plan (rồi human review)
- run_api / run_ui / run_chaos / run_performance: chạy đúng lớp test khi được yêu cầu

Dùng `with_structured_output` nên tương thích cả Antigravity (JSON mode) lẫn OpenAI.
Không có `is_conversational_query`, không có danh sách keyword cứng.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.llm_factory import create_chat_llm, get_active_provider

Intent = Literal["answer", "plan", "run_api", "run_ui", "run_chaos", "run_performance"]

ROUTER_SYSTEM = """You are the intent router for QC Agent, an automated software testing assistant.
Read the user's message and choose EXACTLY ONE intent describing what they want:

- "answer": greetings, questions, explanations, help, or anything that is NOT an explicit request to plan or run tests. (e.g. "hi", "what is chaos testing?", "explain this report")
- "plan": the user wants to create or update a test plan from a requirement/spec. (e.g. "create a test plan for the login API")
- "run_api": the user explicitly wants to run API tests only.
- "run_ui": the user explicitly wants to run UI/E2E tests only.
- "run_chaos": the user explicitly wants to run chaos/resilience tests only.
- "run_performance": the user explicitly wants to run performance/load tests only.

Rules:
- When in doubt, choose "answer". Only choose "plan"/"run_*" when the user clearly asks for it.
- A single word or greeting ("hi", "h", "ok") is always "answer".
"""


class RouteDecision(BaseModel):
    intent: Intent = Field(description="The single chosen intent")


def route_intent(user_message: str) -> Intent:
    """Trả về intent do LLM quyết định. Fallback 'answer' nếu không có provider."""
    text = (user_message or "").strip()
    if not text:
        return "answer"
    if get_active_provider() == "none":
        return "answer"

    try:
        llm = create_chat_llm(role="default", temperature=0)
        router = llm.with_structured_output(RouteDecision)
        decision = router.invoke(
            [SystemMessage(content=ROUTER_SYSTEM), HumanMessage(content=text)]
        )
        if isinstance(decision, RouteDecision):
            return decision.intent
        if isinstance(decision, dict) and decision.get("intent"):
            return decision["intent"]  # type: ignore[return-value]
    except Exception:
        pass
    return "answer"
