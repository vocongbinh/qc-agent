"""Helper fetch available models dynamically from Google Antigravity backend."""

import logging
from typing import Any

import httpx

from auth.antigravity import (
    CLOUD_CODE_ENDPOINT,
    get_antigravity_user_agent,
    get_valid_antigravity_credentials,
)

logger = logging.getLogger(__name__)

# Danh sách seed mặc định dùng khi chưa đăng nhập hoặc offline
DEFAULT_ANTIGRAVITY_MODELS = [
    {
        "id": "gemini-2.5-flash",
        "name": "gemini-2.5-flash",
        "desc": "Tốc độ cực nhanh, ổn định cao, không lo lỗi 503 (Khuyên dùng)",
        "provider": "antigravity",
    },
    {
        "id": "gemini-2.5-pro",
        "name": "gemini-2.5-pro",
        "desc": "Mô hình suy luận sâu, phân tích code và kiến trúc phức tạp",
        "provider": "antigravity",
    },
    {
        "id": "gemini-3.1-pro",
        "name": "gemini-3.1-pro",
        "desc": "Bản thử nghiệm Gemini 3 Pro thế hệ mới",
        "provider": "antigravity",
    },
    {
        "id": "claude-sonnet-4-5",
        "name": "claude-sonnet-4-5",
        "desc": "Anthropic Claude 3.5/4.5 Sonnet qua hạ tầng Antigravity",
        "provider": "antigravity",
    },
]


def fetch_live_antigravity_models() -> list[dict[str, Any]]:
    """Gọi trực tiếp Google Cloud Code Assist để lấy danh sách model thời gian thực."""
    creds = get_valid_antigravity_credentials()
    if not creds or not creds.get("access_token"):
        return DEFAULT_ANTIGRAVITY_MODELS

    token = creds["access_token"]
    url = f"{CLOUD_CODE_ENDPOINT}/v1internal:fetchAvailableModels"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": get_antigravity_user_agent(),
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, headers=headers, json={})
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("models", {})
                discovered = []
                for model_id, info in raw_models.items():
                    if info.get("isInternal") is True:
                        continue
                    # Bỏ qua các model nội bộ không mong muốn
                    if model_id in ("chat_20706", "chat_23310"):
                        continue
                    display_name = info.get("displayName") or model_id
                    thinking = " (Suy luận)" if info.get("supportsThinking") else ""
                    desc = f"{display_name}{thinking}"
                    discovered.append({
                        "id": model_id,
                        "name": model_id,
                        "desc": desc,
                        "provider": "antigravity",
                    })
                if discovered:
                    # Sắp xếp ưu tiên flash/pro lên đầu
                    discovered.sort(
                        key=lambda x: (
                            0 if "flash" in x["id"] else (1 if "pro" in x["id"] else (2 if "claude" in x["id"] else 3)),
                            x["id"],
                        )
                    )
                    return discovered
    except Exception as e:
        logger.warning(f"Không thể lấy live models từ Antigravity: {e}")

    return DEFAULT_ANTIGRAVITY_MODELS
