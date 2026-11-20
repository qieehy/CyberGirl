"""Uses the existing authenticated model provider with no desktop bridge."""

import asyncio

from ...bootstrap import open_live_application
from ...context import LOCAL_SCOPE, ChatSettings
from ...providers.codex.catalog import fetch_catalog
from ...social.models import Decision, SocialError
from ...social.persona import assemble


class ClawBotModel:
    def __init__(self, model, directory):
        self.model, self.directory = model, directory
        self.lock = asyncio.Lock()

    async def catalog(self):
        async with self.lock:
            return await self._catalog()

    async def _catalog(self):
        async with open_live_application(self.model, self.directory) as app:
            epoch = app.tokens.context_key()
            catalog = await fetch_catalog(await app.tokens.get_session())
            if catalog.context_key != epoch or app.tokens.context_key() != epoch:
                raise SocialError("model_identity_changed")
            return catalog

    async def generate(self, text, history, persona, config):
        async with self.lock:
            return await self._generate(text, history, persona, config)

    async def _generate(self, text, history, persona, config):
        system, content, _ = assemble(persona, text, history)
        system += f"\n回复写成单行，不超过{config['reply_max_chars']}个字符，不使用emoji。语音转写可能有误。"
        async with open_live_application(
            self.model,
            self.directory,
            ChatSettings(
                system_prompt=system, max_input_chars=20000, context_chars=30000, history_pairs=1
            ),
        ) as app:
            epoch = app.tokens.context_key()
            catalog = await fetch_catalog(await app.tokens.get_session())
            if not catalog.contains(self.model) or catalog.context_key != epoch:
                raise SocialError("model_unavailable")

            async def defer(_):
                return False

            result = await app.engine.turn(LOCAL_SCOPE, content, defer)
            if app.tokens.context_key() != epoch:
                raise SocialError("model_identity_changed")
            return Decision.parse(result.draft.text), dict(
                inference_calls=1, model_http_attempts=app.provider.http_attempts, phase="complete"
            )
