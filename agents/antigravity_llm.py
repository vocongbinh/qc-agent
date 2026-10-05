"""LangChain Chat Model Adapter cho Google Antigravity (Oh My Pi compatible).

Tự động sử dụng token subscription của Google Gemini / Antigravity
thông qua OAuth2 PKCE và endpoint Cloud Code Assist nội bộ.
"""

import json
import logging
import re
import time
from typing import Any, AsyncIterator, Iterator, Optional, Type, Union
import uuid

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

from auth.antigravity import (
    CLOUD_CODE_ENDPOINT,
    get_antigravity_user_agent,
    get_valid_antigravity_credentials,
)

logger = logging.getLogger(__name__)


def convert_message_to_gemini(message: BaseMessage) -> dict[str, Any]:
    """Chuyển đổi LangChain BaseMessage sang định dạng Gemini Content."""
    if isinstance(message, HumanMessage):
        role = "user"
        parts: list[dict[str, Any]] = []
        if isinstance(message.content, str):
            parts.append({"text": message.content})
        elif isinstance(message.content, list):
            for item in message.content:
                if isinstance(item, str):
                    parts.append({"text": item})
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:image/"):
                            header, b64data = url.split(";base64,", 1)
                            mime_type = header.replace("data:", "")
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64data,
                                }
                            })
        return {"role": role, "parts": parts}
    elif isinstance(message, AIMessage):
        return {"role": "model", "parts": [{"text": str(message.content)}]}
    elif isinstance(message, SystemMessage):
        return {"role": "user", "parts": [{"text": str(message.content)}]}
    elif isinstance(message, ChatMessage):
        role = "model" if message.role in ("assistant", "model") else "user"
        return {"role": role, "parts": [{"text": str(message.content)}]}
    else:
        return {"role": "user", "parts": [{"text": str(message.content)}]}


def parse_sse_event_chunk(chunk_str: str) -> tuple[str, str | None]:
    """Trích xuất visible text và error từ một chunk json SSE của Cloud Code Assist."""
    visible_text = ""
    error_msg = None
    try:
        data = json.loads(chunk_str)
        if "error" in data:
            err = data["error"]
            error_msg = err.get("message") or err.get("status") or str(err)
            return "", error_msg

        response = data.get("response", {})
        candidates = response.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                # Bỏ qua suy nghĩ nội bộ (thinking) nếu có
                if part.get("thought") is True:
                    continue
                text = part.get("text")
                if text:
                    visible_text += text
    except Exception:
        pass
    return visible_text, error_msg


class ChatAntigravity(BaseChatModel):
    """ChatModel của LangChain kết nối tới Google Antigravity."""

    model_name: str = Field(default="gemini-2.5-pro", alias="model")
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    project_id: Optional[str] = None
    timeout: float = 60.0

    @property
    def _llm_type(self) -> str:
        return "google-antigravity"

    def _get_auth_headers(self) -> tuple[dict[str, str], str]:
        creds = get_valid_antigravity_credentials()
        if not creds or not creds.get("access_token"):
            raise RuntimeError(
                "Chưa đăng nhập Google Antigravity. Vui lòng chạy lệnh: python main.py login antigravity"
            )
        token = creds["access_token"]
        project = self.project_id or creds.get("project_id", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": get_antigravity_user_agent(),
        }
        return headers, project

    def _build_payload(
        self,
        messages: list[BaseMessage],
        response_mime_type: Optional[str] = None,
        response_schema: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        headers, project = self._get_auth_headers()

        system_instruction = None
        gemini_contents = []

        for msg in messages:
            if isinstance(msg, SystemMessage) and system_instruction is None:
                # OMP lưu ý: Antigravity đánh dấu systemInstruction role là user
                system_instruction = {"role": "user", "parts": [{"text": str(msg.content)}]}
            else:
                gemini_contents.append(convert_message_to_gemini(msg))

        if not gemini_contents and system_instruction:
            gemini_contents = [system_instruction]
            system_instruction = None

        gen_config: dict[str, Any] = {
            "temperature": self.temperature,
        }
        if self.max_tokens:
            gen_config["maxOutputTokens"] = self.max_tokens
        if response_mime_type:
            gen_config["responseMimeType"] = response_mime_type
        if response_schema:
            gen_config["responseSchema"] = response_schema

        req_body: dict[str, Any] = {
            "contents": gemini_contents,
            "generationConfig": gen_config,
        }
        if system_instruction:
            req_body["systemInstruction"] = system_instruction

        request_id = f"agent/{uuid.uuid4()}/{int(time.time() * 1000)}/{uuid.uuid4()}/1"
        payload = {
            "project": project,
            "requestId": request_id,
            "request": req_body,
            "model": self.model_name,
            "userAgent": "antigravity",
            "requestType": "agent",
        }
        return payload

    def _process_sse_stream(self, lines: Iterator[str]) -> str:
        full_text = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                raw_json = line[5:].strip()
                if raw_json == "[DONE]":
                    break
                text_part, err = parse_sse_event_chunk(raw_json)
                if err:
                    raise RuntimeError(f"Google Antigravity lỗi: {err}")
                full_text += text_part
        return full_text

    async def _aprocess_sse_stream(self, lines: AsyncIterator[str]) -> str:
        full_text = ""
        async for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                raw_json = line[5:].strip()
                if raw_json == "[DONE]":
                    break
                text_part, err = parse_sse_event_chunk(raw_json)
                if err:
                    raise RuntimeError(f"Google Antigravity lỗi: {err}")
                full_text += text_part
        return full_text

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        headers, _ = self._get_auth_headers()
        url = f"{CLOUD_CODE_ENDPOINT}/v1internal:streamGenerateContent?alt=sse"
        payload = self._build_payload(
            messages,
            response_mime_type=kwargs.get("response_mime_type"),
            response_schema=kwargs.get("response_schema"),
        )

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 503 or resp.status_code == 429:
                    err_content = resp.read().decode("utf-8", errors="replace")
                    fallback = "gemini-2.5-flash"
                    if self.model_name != fallback and "CAPACITY_EXHAUSTED" in err_content:
                        logger.warning(
                            f"Model {self.model_name} quá tải ({resp.status_code}). Tự động fallback sang {fallback}..."
                        )
                        payload["model"] = fallback
                        with client.stream("POST", url, headers=headers, json=payload) as retry_resp:
                            if retry_resp.status_code == 200:
                                content = self._process_sse_stream(retry_resp.iter_lines())
                                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
                    raise RuntimeError(
                        f"Antigravity API thất bại ({resp.status_code}): {err_content}"
                    )
                elif resp.status_code != 200:
                    err_content = resp.read().decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Antigravity API thất bại ({resp.status_code}): {err_content}"
                    )
                content = self._process_sse_stream(resp.iter_lines())

        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        headers, _ = self._get_auth_headers()
        url = f"{CLOUD_CODE_ENDPOINT}/v1internal:streamGenerateContent?alt=sse"
        payload = self._build_payload(
            messages,
            response_mime_type=kwargs.get("response_mime_type"),
            response_schema=kwargs.get("response_schema"),
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 503 or resp.status_code == 429:
                    err_content = (await resp.aread()).decode("utf-8", errors="replace")
                    fallback = "gemini-2.5-flash"
                    if self.model_name != fallback and "CAPACITY_EXHAUSTED" in err_content:
                        logger.warning(
                            f"Model {self.model_name} quá tải ({resp.status_code}). Tự động fallback sang {fallback}..."
                        )
                        payload["model"] = fallback
                        async with client.stream("POST", url, headers=headers, json=payload) as retry_resp:
                            if retry_resp.status_code == 200:
                                content = await self._aprocess_sse_stream(retry_resp.aiter_lines())
                                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
                    raise RuntimeError(
                        f"Antigravity API thất bại ({resp.status_code}): {err_content}"
                    )
                elif resp.status_code != 200:
                    err_content = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Antigravity API thất bại ({resp.status_code}): {err_content}"
                    )
                content = await self._aprocess_sse_stream(resp.aiter_lines())

        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(
        self,
        schema: Union[dict[str, Any], Type[BaseModel]],
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable:
        """Hỗ trợ Pydantic model và JSON Schema trực tiếp thông qua JSON Mode."""
        is_pydantic = isinstance(schema, type) and issubclass(schema, BaseModel)

        def _format_input(input_val: Any) -> list[BaseMessage]:
            schema_json = (
                json.dumps(schema.model_json_schema(), ensure_ascii=False)
                if is_pydantic
                else json.dumps(schema, ensure_ascii=False)
            )
            instruction = (
                f"\n\nBẠN BẮT BUỘC PHẢI TRẢ LỜI ĐÚNG ĐỊNH DẠNG JSON KHỚP VỚI JSON SCHEMA SAU ĐÂY:\n"
                f"```json\n{schema_json}\n```\n"
                f"Không thêm bất kỳ lời dẫn, giải thích hoặc markdown thừa ngoài chuỗi JSON hợp lệ."
            )

            if isinstance(input_val, list):
                msgs = list(input_val)
                last_msg = msgs[-1]
                if isinstance(last_msg, HumanMessage):
                    new_content = f"{last_msg.content}\n{instruction}"
                    msgs[-1] = HumanMessage(content=new_content)
                else:
                    msgs.append(HumanMessage(content=instruction))
                return msgs
            elif isinstance(input_val, str):
                return [HumanMessage(content=f"{input_val}\n{instruction}")]
            return [HumanMessage(content=str(input_val))]

        def _parse_output(msg: AIMessage) -> Any:
            text = str(msg.content).strip()
            # Xử lý nếu model bọc trong ```json ... ```
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
                text = text.strip()

            parsed_dict = json.loads(text)
            if is_pydantic:
                return schema.model_validate(parsed_dict)
            return parsed_dict

        async def _async_run(input_val: Any) -> Any:
            msgs = _format_input(input_val)
            res = await self._agenerate(msgs, response_mime_type="application/json")
            ai_msg = res.generations[0].message
            return _parse_output(ai_msg)

        def _sync_run(input_val: Any) -> Any:
            msgs = _format_input(input_val)
            res = self._generate(msgs, response_mime_type="application/json")
            ai_msg = res.generations[0].message
            return _parse_output(ai_msg)

        return RunnableLambda(func=_sync_run, afunc=_async_run)
