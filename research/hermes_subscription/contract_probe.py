"""Isolated, original text-only protocol probe. Not a production provider.

No HTTP, login, token storage, Hermes imports, or real messages. The caller
injects a synthetic stream and the actual AI_agent result model types.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ContractError(ValueError):
    """A response cannot safely become a deliverable text result."""


def build_request(messages: list[dict], tools: list[dict] | None, *, model: str) -> dict:
    if not isinstance(model, str) or not model.strip():
        raise ContractError("A selected model is required")
    if tools not in (None, []):
        raise ContractError("Research probe is text-only")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ContractError("Expected bounded conversation messages")
    instructions = ""
    items = []
    total_chars = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or set(message) - {"role", "content"}:
            raise ContractError("Unsupported conversation shape")
        role, text = message.get("role"), message.get("content")
        if not isinstance(text, str):
            raise ContractError("Only text content is supported")
        total_chars += len(text)
        if total_chars > 100_000:
            raise ContractError("Conversation exceeds research limit")
        if index == 0 and role == "system":
            instructions = text
            continue
        if role not in {"user", "assistant"}:
            raise ContractError("Only the trusted initial system message may set instructions")
        items.append({"type": "message", "role": role, "content": [
            {"type": "output_text" if role == "assistant" else "input_text", "text": text}
        ]})
    if not instructions.strip() or not items or items[-1]["role"] != "user":
        raise ContractError("Expected explicit instructions and a user turn")
    return {"model": model.strip(), "instructions": instructions, "input": items,
            "store": False, "stream": True}


def _check_output(items: object) -> list[dict]:
    if not isinstance(items, list) or len(items) > 32:
        raise ContractError("Expected bounded output items")
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"}:
            raise ContractError("Unexpected tool or output item")
    return items


def _final_text(items: list[dict]) -> str:
    parts = []
    for item in items:
        if item["type"] == "reasoning":
            continue
        if item.get("role") != "assistant" or item.get("status") != "completed":
            raise ContractError("Incomplete assistant output")
        phase = item.get("phase")
        if phase in {"analysis", "commentary"}:
            continue
        if phase not in {None, "final", "final_answer"}:
            raise ContractError("Unknown assistant phase")
        content = item.get("content")
        if not isinstance(content, list):
            raise ContractError("Missing assistant content")
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise ContractError("Unsupported assistant content")
            value = part.get("text")
            if not isinstance(value, str):
                raise ContractError("Output text must be text")
            parts.append(value)
    result = "\n".join(parts).strip()
    if not result or len(result) > 10_000:
        raise ContractError("Empty or excessive final answer")
    return result


async def consume_completed(
    stream: AsyncIterator[dict], *, models: Any, selected_model: str,
    request_id: str | None = None,
) -> Any:
    """Return AI_agent.LLMResult only after a confirmed successful terminal.

    Delta-only streams without authoritative message items are deliberately
    rejected by this first research slice. No partial text is emitted.
    """
    completed_items: dict[int, dict] = {}
    seen_response_id = None
    delta_chars = 0
    try:
        count = 0
        async for event in stream:
            count += 1
            if count > 512 or not isinstance(event, dict):
                raise ContractError("Invalid or excessive stream events")
            kind = event.get("type")
            if kind in {"error", "response.failed", "response.incomplete", "response.cancelled"}:
                raise ContractError("Provider did not complete successfully")
            if kind == "response.created":
                created = event.get("response")
                if not isinstance(created, dict) or not isinstance(created.get("id"), str):
                    raise ContractError("Invalid response identity")
                if seen_response_id is not None and seen_response_id != created["id"]:
                    raise ContractError("Response identity changed")
                seen_response_id = created["id"]
            elif kind == "response.output_text.delta":
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise ContractError("Invalid text delta")
                delta_chars += len(delta)
                if delta_chars > 20_000:
                    raise ContractError("Text stream exceeds research limit")
            elif kind in {"response.output_item.added", "response.output_item.done"}:
                item = event.get("item")
                _check_output([item])
                if kind.endswith(".done"):
                    index = event.get("output_index")
                    if type(index) is not int or not 0 <= index < 32:
                        raise ContractError("Invalid output index")
                    if index in completed_items and completed_items[index] != item:
                        raise ContractError("Completed output was changed")
                    completed_items[index] = item
            elif kind == "response.completed":
                response = event.get("response")
                if not isinstance(response, dict) or response.get("status") != "completed":
                    raise ContractError("Missing successful response terminal")
                if seen_response_id is not None and response.get("id") != seen_response_id:
                    raise ContractError("Response identity changed")
                raw_output = response.get("output")
                terminal_items = _check_output([] if raw_output is None else raw_output)
                items = ([completed_items[index] for index in sorted(completed_items)]
                         if completed_items else terminal_items)
                text = _final_text(items)
                usage = response.get("usage")
                usage_model = None
                if usage is not None:
                    if not isinstance(usage, dict):
                        raise ContractError("Invalid token usage")
                    try:
                        usage_model = models.TokenUsage(
                            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                            total_tokens=usage["total_tokens"],
                        )
                    except (KeyError, ValueError, TypeError) as exc:
                        raise ContractError("Invalid token usage") from exc
                return models.LLMResult(
                    message=models.AssistantMessage(content=text), usage=usage_model,
                    model=response.get("model") or selected_model, finish_reason="stop",
                    # A Responses response ID is not an HTTP request ID.
                    request_id=request_id,
                )
        raise ContractError("Stream ended before response.completed")
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
