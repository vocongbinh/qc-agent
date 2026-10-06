"""Conversational Assistant for QC Agent (OpenCode ReAct Style).

Xử lý các câu chào hỏi, hỏi đáp thông thường (greetings, general Q&A)
mà không tự tiện kích hoạt luồng lập Test Plan giả định.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm_factory import create_chat_llm, get_active_provider

ASSISTANT_SYSTEM = """You are QC Agent, an expert AI Software Quality Control and Testing Engineer (OpenCode / Claude Code style).

Guidelines for conversation:
1. **Match User Language:** Respond in the language used by the user (Vietnamese if user writes in Vietnamese, English if in English).
2. **Natural & Direct Conversation:**
   - For simple greetings (e.g., 'hi', 'hello', 'chào bạn'): Respond warmly and briefly in 1-2 sentences. Do NOT dump a full list of features unless asked.
   - When asked about capabilities (e.g., 'bạn có chức năng gì', 'what can you do?'): Clearly explain your core features concisely:
     • API Testing (REST, OpenAPI validation, multi-step flows)
     • UI & E2E Testing (Playwright automated browser tests)
     • Chaos & Resilience Testing (Docker failure injection, Toxiproxy latency)
     • Performance Testing (k6 load tests, p95 latency)
   - When asked technical QA/engineering questions: Answer their specific question directly with technical depth and practical examples.
3. **Developer-Oriented Tone:** Be friendly, technical, direct, and concise. Never use robotic boilerplate. Do NOT output raw TestPlan JSON during general conversation.
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
