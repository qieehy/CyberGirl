"""Collect authoritative text only after explicit successful Responses termination."""

from collections.abc import AsyncIterator

from llm.models import AssistantMessage, LLMResult, TokenUsage

from .config import Limits
from .errors import ErrorCode, ProviderError

_FAILURES = {"error", "response.failed", "response.incomplete", "response.cancelled"}
_PASSIVE = {
    "response.in_progress", "response.queued", "response.output_text.done", "response.metadata",
    "response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
    "response.reasoning_text.delta", "response.reasoning_text.done",
}


def _items(value: object, limits: Limits) -> list[dict]:
    if not isinstance(value, list):
        raise ProviderError(ErrorCode.PROTOCOL)
    if len(value) > limits.output_items:
        raise ProviderError(ErrorCode.LIMIT)
    for item in value:
        if (not isinstance(item, dict) or not isinstance(item.get("type"), str)
                or item["type"] not in {"message", "reasoning"}):
            raise ProviderError(ErrorCode.PROTOCOL)
    return value


def _text(items: list[dict], limits: Limits) -> str:
    parts: list[str] = []
    length = 0
    for item in items:
        if item["type"] == "reasoning":
            continue
        if item.get("role") != "assistant" or item.get("status") != "completed":
            raise ProviderError(ErrorCode.PROTOCOL)
        phase = item.get("phase")
        if phase is not None and (not isinstance(phase, str) or phase not in {"analysis", "commentary", "final", "final_answer"}):
            raise ProviderError(ErrorCode.PROTOCOL)
        content = item.get("content")
        if not isinstance(content, list):
            raise ProviderError(ErrorCode.PROTOCOL)
        for part in content:
            if isinstance(part, dict) and part.get("type") == "refusal":
                raise ProviderError(ErrorCode.REFUSAL)
            if (not isinstance(part, dict) or part.get("type") != "output_text"
                    or not isinstance(part.get("text"), str)):
                raise ProviderError(ErrorCode.PROTOCOL)
            if phase not in {"analysis", "commentary"}:
                length += len(part["text"]) + bool(parts)
                if length > limits.output_chars:
                    raise ProviderError(ErrorCode.LIMIT)
                parts.append(part["text"])
    text = "\n".join(parts).strip()
    if not text:
        raise ProviderError(ErrorCode.PROTOCOL)
    return text


def _usage(value: object) -> TokenUsage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProviderError(ErrorCode.PROTOCOL)
    try:
        return TokenUsage(input_tokens=value["input_tokens"], output_tokens=value["output_tokens"],
                          total_tokens=value["total_tokens"])
    except (KeyError, ValueError, TypeError):
        raise ProviderError(ErrorCode.PROTOCOL) from None


async def collect_result(
    events: AsyncIterator[dict], *, limits: Limits, selected_model: str,
    request_id: str | None,
) -> LLMResult:
    completed: dict[int, dict] = {}
    item_ids: dict[str, int] = {}
    response_id: str | None = None
    delta_chars = 0
    count = 0
    async for event in events:
        count += 1
        if count > limits.events:
            raise ProviderError(ErrorCode.LIMIT)
        kind = event.get("type")
        if kind in _FAILURES:
            raise ProviderError(ErrorCode.INCOMPLETE)
        event_response_id = event.get("response_id")
        if event_response_id is not None:
            if (not isinstance(event_response_id, str) or not event_response_id
                    or (response_id is not None and event_response_id != response_id)):
                raise ProviderError(ErrorCode.PROTOCOL)
            response_id = event_response_id
        if kind in {"response.created", "response.in_progress", "response.queued", "response.completed"}:
            response = event.get("response")
            if not isinstance(response, dict) or not isinstance(response.get("id"), str) or not response["id"]:
                raise ProviderError(ErrorCode.PROTOCOL)
            if response_id is not None and response_id != response["id"]:
                raise ProviderError(ErrorCode.PROTOCOL)
            response_id = response["id"]
        if kind == "response.output_text.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise ProviderError(ErrorCode.PROTOCOL)
            delta_chars += len(delta)
            if delta_chars > limits.delta_chars:
                raise ProviderError(ErrorCode.LIMIT)
        elif kind in {"response.output_item.added", "response.output_item.done"}:
            item = _items([event.get("item")], limits)[0]
            index = event.get("output_index")
            if type(index) is not int or not 0 <= index < limits.output_items:
                raise ProviderError(ErrorCode.PROTOCOL)
            if kind.endswith(".done"):
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    raise ProviderError(ErrorCode.PROTOCOL)
                if ((index in completed and completed[index] != item)
                        or (item_id in item_ids and item_ids[item_id] != index)):
                    raise ProviderError(ErrorCode.PROTOCOL)
                completed[index] = item
                item_ids[item_id] = index
        elif kind in {"response.content_part.added", "response.content_part.done"}:
            part = event.get("part")
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise ProviderError(ErrorCode.PROTOCOL)
        elif kind in {"response.refusal.delta", "response.refusal.done"}:
            raise ProviderError(ErrorCode.REFUSAL)
        elif kind == "response.completed":
            response = event["response"]
            if response.get("status") != "completed" or response.get("error") is not None:
                raise ProviderError(ErrorCode.INCOMPLETE)
            raw_output = response.get("output")
            terminal = _items([] if raw_output is None else raw_output, limits)
            if terminal:
                for index, done in completed.items():
                    if index >= len(terminal) or terminal[index] != done:
                        raise ProviderError(ErrorCode.PROTOCOL)
                items = terminal
            else:
                items = [completed[index] for index in sorted(completed)]
            model = response.get("model", selected_model)
            if not isinstance(model, str) or not model.strip():
                raise ProviderError(ErrorCode.PROTOCOL)
            return LLMResult(message=AssistantMessage(content=_text(items, limits)),
                             usage=_usage(response.get("usage")), model=model,
                             finish_reason="stop", request_id=request_id)
        elif kind != "response.created" and kind not in _PASSIVE:
            raise ProviderError(ErrorCode.PROTOCOL)
    raise ProviderError(ErrorCode.INCOMPLETE)
