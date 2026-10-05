"""Conversational Assistant for QC Agent (OpenCode ReAct Style).

Xử lý các câu chào hỏi, hỏi đáp thông thường (greetings, general Q&A)
mà không tự tiện kích hoạt luồng lập Test Plan giả định.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm_factory import create_chat_llm, get_active_provider

ASSISTANT_SYSTEM = """You are QC Agent, an expert AI Quality Control and Software Testing Engineer.
When users greet you or ask general questions, respond cordially and explain your capabilities concisely:
1. API Testing (REST, OpenAPI, contract validation)
2. UI / E2E Testing (Automated Playwright flows)
3. Chaos / Resilience Testing (Docker container failures, Toxiproxy latency & cuts)
4. Performance Testing (k6 load testing, p95 latency)

Encourage the user to provide a specific testing requirement (e.g., 'Test user login API with valid credentials' or 'Run chaos test on Redis').
Keep your answer friendly, developer-oriented, and concise. Do NOT generate a structured Test Plan or test case JSON.
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
