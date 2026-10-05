"""Unit tests cho Google Antigravity Auth, Adapter & LLM Factory."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from agents.antigravity_llm import (
    ChatAntigravity,
    convert_message_to_gemini,
    parse_sse_event_chunk,
)
from agents.llm_factory import create_chat_llm, get_active_provider
from auth.antigravity import (
    generate_pkce,
    get_antigravity_user_agent,
    get_valid_antigravity_credentials,
    load_all_credentials,
    save_credentials,
)


# ==================== Auth Tests ====================


def test_generate_pkce():
    verifier, challenge = generate_pkce()
    assert isinstance(verifier, str) and len(verifier) >= 43
    assert isinstance(challenge, str) and len(challenge) >= 43
    assert verifier != challenge


def test_get_antigravity_user_agent():
    ua = get_antigravity_user_agent()
    assert "antigravity/hub/" in ua
    assert "aidev_client" in ua
    assert "cl=963137146" in ua


def test_save_and_load_credentials(tmp_path, monkeypatch):
    creds_file = tmp_path / "creds.json"
    monkeypatch.setenv("QC_AGENT_CREDENTIALS_PATH", str(creds_file))

    assert load_all_credentials() == {}

    test_data = {
        "access_token": "ya29.test",
        "refresh_token": "1//refresh",
        "expires_at": time.time() + 3600,
        "email": "test@gmail.com",
    }
    save_credentials("antigravity", test_data)

    loaded = load_all_credentials()
    assert "antigravity" in loaded
    assert loaded["antigravity"]["email"] == "test@gmail.com"


def test_get_valid_antigravity_credentials_unexpired(tmp_path, monkeypatch):
    creds_file = tmp_path / "creds.json"
    monkeypatch.setenv("QC_AGENT_CREDENTIALS_PATH", str(creds_file))

    test_data = {
        "access_token": "ya29.valid",
        "refresh_token": "1//refresh",
        "expires_at": time.time() + 1000,
    }
    save_credentials("antigravity", test_data)

    valid = get_valid_antigravity_credentials()
    assert valid is not None
    assert valid["access_token"] == "ya29.valid"


def test_get_valid_antigravity_credentials_auto_refresh(tmp_path, monkeypatch):
    creds_file = tmp_path / "creds.json"
    monkeypatch.setenv("QC_AGENT_CREDENTIALS_PATH", str(creds_file))

    # Đã hết hạn
    test_data = {
        "access_token": "ya29.expired",
        "refresh_token": "1//refresh",
        "expires_at": time.time() - 100,
    }
    save_credentials("antigravity", test_data)

    # Mock refresh_access_token
    with patch("auth.antigravity.refresh_access_token") as mock_refresh:
        mock_refresh.return_value = {
            "access_token": "ya29.new_token",
            "expires_in": 3600,
        }
        valid = get_valid_antigravity_credentials()
        assert valid is not None
        assert valid["access_token"] == "ya29.new_token"
        assert valid["expires_at"] > time.time()


def test_antigravity_client_credentials_env_override(monkeypatch):
    import importlib
    import auth.antigravity as ag
    monkeypatch.setenv("ANTIGRAVITY_CLIENT_ID", "custom-client-id")
    monkeypatch.setenv("ANTIGRAVITY_CLIENT_SECRET", "custom-client-secret")
    importlib.reload(ag)
    try:
        assert ag.CLIENT_ID == "custom-client-id"
        assert ag.CLIENT_SECRET == "custom-client-secret"
    finally:
        monkeypatch.delenv("ANTIGRAVITY_CLIENT_ID", raising=False)
        monkeypatch.delenv("ANTIGRAVITY_CLIENT_SECRET", raising=False)
        importlib.reload(ag)

# ==================== Adapter & Message Conversion ====================


def test_convert_messages_to_gemini():
    human_msg = HumanMessage(content="Hello world")
    gemini_human = convert_message_to_gemini(human_msg)
    assert gemini_human["role"] == "user"
    assert gemini_human["parts"] == [{"text": "Hello world"}]

    ai_msg = AIMessage(content="I am Gemini")
    gemini_ai = convert_message_to_gemini(ai_msg)
    assert gemini_ai["role"] == "model"
    assert gemini_ai["parts"] == [{"text": "I am Gemini"}]

    sys_msg = SystemMessage(content="Be helpful")
    gemini_sys = convert_message_to_gemini(sys_msg)
    assert gemini_sys["role"] == "user"
    assert gemini_sys["parts"] == [{"text": "Be helpful"}]


def test_convert_multimodal_message_to_gemini():
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Describe image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ]
    )
    res = convert_message_to_gemini(msg)
    assert res["role"] == "user"
    assert len(res["parts"]) == 2
    assert res["parts"][0] == {"text": "Describe image"}
    assert res["parts"][1]["inlineData"]["mimeType"] == "image/png"
    assert res["parts"][1]["inlineData"]["data"] == "iVBORw0KGgo="


def test_parse_sse_event_chunk():
    # Chunk bình thường
    chunk = json.dumps({
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Hello "},
                            {"text": "world!"},
                        ]
                    }
                }
            ]
        }
    })
    text, err = parse_sse_event_chunk(chunk)
    assert text == "Hello world!"
    assert err is None

    # Chunk chứa suy nghĩ (thought) - phải bỏ qua
    thinking_chunk = json.dumps({
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Thinking steps...", "thought": True},
                            {"text": "Final answer"},
                        ]
                    }
                }
            ]
        }
    })
    text, err = parse_sse_event_chunk(thinking_chunk)
    assert text == "Final answer"
    assert err is None

    # Chunk lỗi
    error_chunk = json.dumps({"error": {"message": "Resource exhausted", "code": 429}})
    text, err = parse_sse_event_chunk(error_chunk)
    assert text == ""
    assert err == "Resource exhausted"


class SampleSchema(BaseModel):
    summary: str
    items_count: int


def test_chat_antigravity_structured_output_sync():
    """Kiểm tra parser with_structured_output của ChatAntigravity."""
    chat = ChatAntigravity(model="gemini-2.5-pro")

    # Mock _generate để trả về JSON
    mock_json = '{"summary": "Test thành công", "items_count": 5}'
    with patch.object(chat, "_generate") as mock_gen:
        mock_gen.return_value = MagicMock(
            generations=[MagicMock(message=AIMessage(content=mock_json))]
        )
        structured_model = chat.with_structured_output(SampleSchema)
        result = structured_model.invoke([HumanMessage(content="Test plan")])

        assert isinstance(result, SampleSchema)
        assert result.summary == "Test thành công"
        assert result.items_count == 5


# ==================== LLM Factory Tests ====================


def test_llm_factory_selection(monkeypatch):
    from config.settings import settings

    # 1. Khi có credentials Antigravity -> chọn antigravity
    monkeypatch.setattr(settings, "llm_provider", "auto")
    monkeypatch.setattr(settings, "openai_api_key", None)
    with patch("agents.llm_factory.is_antigravity_available", return_value=True):
        assert get_active_provider() == "antigravity"
        llm = create_chat_llm(role="planner")
        assert isinstance(llm, ChatAntigravity)

    # 2. Khi không có Antigravity nhưng có OPENAI_API_KEY -> chọn openai
    with patch("agents.llm_factory.is_antigravity_available", return_value=False):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        assert get_active_provider() == "openai"
        llm = create_chat_llm(role="planner")
        assert llm.__class__.__name__ == "ChatOpenAI"
