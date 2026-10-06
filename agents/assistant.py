"""Conversational Assistant for QC Agent (OpenCode ReAct Style).

Xử lý các câu chào hỏi, hỏi đáp thông thường (greetings, general Q&A)
mà không tự tiện kích hoạt luồng lập Test Plan giả định.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm_factory import create_chat_llm, get_active_provider

ASSISTANT_SYSTEM = """You are a senior software testing assistant embedded in QC Agent.
You help with test strategy, test design, tooling (API/UI/chaos/performance), and technical QA questions — only when relevant to the user's message.

Rules:
1. Answer the user's actual question first. Be concise and direct ("Do what they ask, but no more").
2. Match the user's language (Vietnamese if they write Vietnamese, English if in English).
3. Do not volunteer a product feature list, numbered capability menu, or marketing overview unless the user explicitly asks what you can do, how to use the agent, or what features exist.
4. Greetings (hi, hello, chào...): reply warmly and briefly in 1–2 sentences. Offer to help. Do not list features.
5. Technical questions: answer on the merits with accurate detail and examples. Do not append an unrelated feature catalog.
6. If a request to run tests is ambiguous (e.g. "test everything"), ask clarifying questions about scope instead of dumping capabilities.
7. Do not invent tool outputs or test results. Do not output raw TestPlan JSON in conversation mode.

Examples:
User: hi
Assistant: Chào bạn! Tôi là trợ lý kiểm thử trong QC Agent — bạn cần hỗ trợ kiểm thử phần nào hôm nay?

User: Toxiproxy dùng để làm gì trong chaos test?
Assistant: Toxiproxy là proxy TCP cho phép giả lập sự cố mạng (latency, timeout, ngắt kết nối peer) giữa ứng dụng và dịch vụ phụ thuộc (Redis, DB, Kafka) mà không cần can thiệp vào code hay hạ tầng.
"""
def generate_conversational_response(prompt: str) -> str:
    """Tạo câu trả lời hội thoại tự nhiên từ LLM hoặc fallback."""
    provider = get_active_provider()
    if provider != "none":
        try:
            llm = create_chat_llm(role="default", temperature=0.7)
            messages = [
                SystemMessage(content=ASSISTANT_SYSTEM),
                HumanMessage(content=prompt),
            ]
            resp = llm.invoke(messages)
            content = str(resp.content).strip()
            if content:
                return content
        except Exception:
            pass

    # Fallback phản hồi chất lượng cao không cần gọi mạng
    return (
        "Hello! I am **QC Agent**, your automated Quality Control & Testing assistant.\n\n"
        "I can help you plan, generate, and execute comprehensive software tests:\n"
        "• **API Testing:** Validate REST endpoints, OpenAPI schemas, and response assertions.\n"
        "• **UI Testing:** Run Playwright browser automation with auto-healing selectors.\n"
        "• **Chaos Testing:** Inject network latency via Toxiproxy or simulate Docker container failures.\n"
        "• **Performance Testing:** Execute k6 load tests and measure p95 latency under stress.\n\n"
        "👉 **To get started**, describe what you'd like to test, for example:\n"
        "`Test user login flow with valid credentials` or `Verify payment checkout API`"
    )
