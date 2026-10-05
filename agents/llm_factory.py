"""LLM Factory cho qc-agent.

Tự động chọn provider phù hợp:
- Nếu đã đăng nhập Google Antigravity -> Sử dụng ChatAntigravity (subscription)
- Nếu cấu hình OPENAI_API_KEY -> Sử dụng ChatOpenAI
"""

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from auth.antigravity import get_valid_antigravity_credentials
from config.settings import settings

logger = logging.getLogger(__name__)


def is_antigravity_available() -> bool:
    """Kiểm tra xem credentials Antigravity đã sẵn sàng hay chưa."""
    try:
        creds = get_valid_antigravity_credentials()
        return creds is not None and bool(creds.get("access_token"))
    except Exception:
        return False


def get_active_provider() -> str:
    """Xác định provider đang được kích hoạt."""
    provider = getattr(settings, "llm_provider", "auto").lower()
    if provider in ("antigravity", "google-antigravity", "gemini"):
        return "antigravity"
    if provider == "openai":
        return "openai"

    # Chế độ auto: Ưu tiên Antigravity subscription nếu đã đăng nhập, ngược lại dùng OpenAI key
    if is_antigravity_available():
        return "antigravity"
    if settings.openai_api_key:
        return "openai"

    return "none"


def create_chat_llm(
    role: str = "default",
    temperature: float | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Tạo instance LLM tùy theo provider đang chọn."""
    provider = get_active_provider()
    temp = temperature if temperature is not None else settings.temperature

    if provider == "antigravity":
        from agents.antigravity_llm import ChatAntigravity

        # Ánh xạ model tương ứng cho Antigravity nếu đang để mặc định gpt-*
        default_model = getattr(settings, "antigravity_model", "gemini-2.5-pro")
        model = default_model
        if role == "reporter":
            model = getattr(settings, "antigravity_reporter_model", "gemini-2.5-flash")
        elif role == "vision":
            model = getattr(settings, "antigravity_vision_model", "gemini-2.5-pro")

        return ChatAntigravity(
            model=model,
            temperature=temp,
            **kwargs,
        )

    # Mặc định OpenAI
    from langchain_openai import ChatOpenAI

    model = settings.default_model
    if role == "planner":
        model = settings.planner_model
    elif role == "generator":
        model = settings.generator_model
    elif role == "reporter":
        model = settings.reporter_model
    elif role == "vision":
        model = settings.vision_model

    return ChatOpenAI(
        model=model,
        temperature=temp,
        api_key=settings.openai_api_key,
        **kwargs,
    )
