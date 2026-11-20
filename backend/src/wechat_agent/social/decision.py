"""One existing AI_agent Runtime turn, with confirmed ledger facts as context."""

import json

from llm.models import AssistantMessage, LLMResult

from ..chat import ChatEngine
from ..context import ChatSettings
from .models import Decision, IncomingEvent
from .persona import Persona, assemble


class RuntimeDecider:
    def __init__(self, model):
        self.model = model
        self.engine = None
        self._closed = False
        self.last_runtime = None

    async def decide(self, event: IncomingEvent, history: list[dict], *, persona: Persona | None = None) -> Decision:
        if self._closed:
            from .models import SocialError

            raise SocialError("closed")
        system, content, _ = assemble(persona or Persona(), event.text, history)

        async def defer_delivery(draft):
            # Only the SQLite delivery acknowledgement may establish a sent fact.
            return False

        async with ChatEngine(self.model, lambda: "social-synthetic-runtime",
                              ChatSettings(system_prompt=system, max_input_chars=20000,
                                           context_chars=30000, history_pairs=1)) as engine:
            self.engine = engine
            try:
                outcome = await engine.turn(event.scope, content, defer_delivery)
            finally:
                self.engine = None
        self.last_runtime = {"class": outcome.runtime_class, "steps": outcome.steps,
                             "model_calls": outcome.model_calls, "status": outcome.status}
        return Decision.parse(outcome.draft.text)

    async def aclose(self):
        self._closed = True
        if self.engine is not None:
            await self.engine.aclose()


class SyntheticModel:
    """Deterministic fixture, no network, OAuth, API key or real user content."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, messages, tools, **kwargs):
        assert not tools
        self.calls += 1
        data = json.loads(messages[-1]["content"])
        persona = json.loads(messages[0]["content"].split("\nPERSONA_JSON:", 1)[1])
        incoming = data["incoming_text"]
        if incoming.startswith("GROUP_MESSAGES_JSON:"):
            group = json.loads(incoming.removeprefix("GROUP_MESSAGES_JSON:"))
            incoming = next((r["text"] for r in reversed(group) if r["trigger"]), "[silent]")
        silent = incoming in {"[silent]", "这条练习不需要回复。"}
        decision = {"action": "SILENT" if silent else "REPLY",
                    "reply_text": "" if silent else persona["name"] + "（模拟）：我收到了这条练习消息。"}
        return LLMResult(AssistantMessage(json.dumps(decision, ensure_ascii=False)), model="social-mock")
