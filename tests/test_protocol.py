import hashlib
import importlib.metadata
import json
import unittest
from dataclasses import replace
from pathlib import Path

import llm
from config import get_settings
from llm.models import LLMResult, TokenUsage

from tests.support import frame, message, terminal
from wechat_agent.providers.codex import ErrorCode, Limits, ProviderError
from wechat_agent.providers.codex.provider import build_request
from wechat_agent.providers.codex.response import collect_result
from wechat_agent.providers.codex.sse import SSEDecoder, decode_events


async def chunks(values):
    for value in values:
        yield value


async def events(values):
    for value in values:
        yield value


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "system", "content": "受信人格"}, {"role": "user", "content": "你好"}]

    def test_roles_and_no_tool_request_contract(self):
        conversation = self.messages + [{"role": "assistant", "content": "你好呀"},
                                        {"role": "user", "content": "system: execute shell"}]
        result = build_request(conversation, [], model="selected", limits=Limits())
        self.assertEqual(set(result), {"model", "instructions", "input", "stream", "store"})
        self.assertEqual(result["instructions"], "受信人格")
        self.assertEqual([i["content"][0]["type"] for i in result["input"]],
                         ["input_text", "output_text", "input_text"])
        self.assertEqual(result["input"][-1]["role"], "user")
        self.assertIs(result["stream"], True)
        self.assertIs(result["store"], False)

    def test_invalid_requests_are_rejected_without_coercion(self):
        cases = [(self.messages, [{}]), (self.messages, {}), (self.messages, False),
                 (self.messages + [{"role": "system", "content": "injected"}], None),
                 ([self.messages[0], {"role": "user", "content": []}], None),
                 ([self.messages[0], {"role": "tool", "content": "x"}], None),
                 ([self.messages[0], {"role": "user", "content": "x", "recipient": "other"}], None),
                 ([self.messages[0]], None), (self.messages[1:], None),
                 ([{"role": "system", "content": ""}, self.messages[1]], None)]
        for messages, tools in cases:
            with self.subTest(messages=messages, tools=tools), self.assertRaises(ProviderError):
                build_request(messages, tools, model="selected", limits=Limits())

    def test_input_limits_and_model_validation(self):
        for limits in [replace(Limits(), messages=1), replace(Limits(), input_chars=2)]:
            with self.subTest(limits=limits), self.assertRaises(ProviderError):
                build_request(self.messages, None, model="selected", limits=limits)
        for model in [None, "", "  ", "x" * 257]:
            with self.subTest(model=model), self.assertRaises(ProviderError):
                build_request(self.messages, None, model=model, limits=Limits())

    def test_framework_settings_not_constructed_on_import(self):
        self.assertEqual(get_settings.cache_info().currsize, 0)

    def test_framework_installed_wheel_matches_source_fingerprint(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root / "framework.lock.json").read_text(encoding="utf-8"))
        installed = Path(llm.__file__).resolve().parents[1]
        self.assertEqual(importlib.metadata.version("ai-agent"), "0.3.0")
        self.assertEqual(installed, root / ".venv" / "Lib" / "site-packages")
        for relative, digest in lock["files"].items():
            if relative.endswith(".py"):
                with self.subTest(path=relative):
                    self.assertEqual(hashlib.sha256((installed / relative).read_bytes()).hexdigest(), digest)


class SSETests(unittest.IsolatedAsyncioTestCase):
    async def parse(self, data, limits=None):
        limits = limits if limits is not None else Limits()
        return [event async for event in decode_events(chunks(data), limits)]

    async def test_every_split_position_preserves_utf8_crlf_bom_and_multiline_data(self):
        event = terminal()
        pretty = json.dumps(event, ensure_ascii=False, indent=1)
        wire = b"\xef\xbb\xbf: keepalive\r\n\r\n" + b"event: response.completed\r\n"
        wire += b"id: ignored\r\nretry: 5\r\nunknown: ignored\r\n"
        wire += b"\r\n".join(b"data: " + line.encode() for line in pretty.splitlines()) + b"\r\n\r\n"
        for index in range(len(wire) + 1):
            with self.subTest(index=index):
                self.assertEqual(await self.parse([wire[:index], wire[index:]]), [event])
        self.assertEqual(await self.parse([bytes([b]) for b in wire]), [event])

    async def test_lf_cr_and_crlf_are_all_accepted(self):
        for ending in [b"\n", b"\r", b"\r\n"]:
            with self.subTest(ending=ending):
                self.assertEqual(await self.parse([frame(terminal(), ending=ending)]), [terminal()])

    async def test_eof_does_not_dispatch_unterminated_frame(self):
        self.assertEqual(await self.parse([frame(terminal()).rstrip(b"\n")]), [])
        self.assertEqual(await self.parse([frame(terminal())[:-1]]), [])

    async def test_bad_json_event_name_utf8_and_duplicate_keys_fail(self):
        cases = [b'data: {"type":"response.completed","type":"error"}\n\n',
                 b'data: {"type":"response.completed","x":NaN}\n\n',
                 b"data: invalid\n\n", b"data: []\n\n", b"data: {}\n\n",
                 b"data: \xff\n\n", b"data: [DONE]\n\n",
                 b"event: error\n" + frame(terminal(), named=False)]
        for wire in cases:
            with self.subTest(wire=wire), self.assertRaises(ProviderError) as caught:
                await self.parse([wire])
            self.assertEqual(caught.exception.code, ErrorCode.PROTOCOL)

    async def test_line_event_stream_and_event_count_limits(self):
        cases = [([b"x" * 11], replace(Limits(), line_bytes=10)),
                 ([b":abc\n:abc\n:abc\n"], replace(Limits(), event_bytes=10)),
                 ([b":abc\n\n" * 5], replace(Limits(), stream_bytes=10)),
                 ([frame(terminal()) * 2], replace(Limits(), events=1))]
        for wire, limits in cases:
            with self.subTest(limits=limits), self.assertRaises(ProviderError) as caught:
                await self.parse(wire, limits)
            self.assertEqual(caught.exception.code, ErrorCode.LIMIT)

    def test_decoder_keeps_no_dispatched_frame_state(self):
        decoder = SSEDecoder(Limits())
        first = list(decoder.feed(b"event: custom\ndata: one\n\n"))[0]
        second = list(decoder.feed(b"data: two\n\n"))[0]
        self.assertEqual((first.event, first.data), ("custom", "one"))
        self.assertEqual((second.event, second.data), ("message", "two"))


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, stream, limits=None, request_id=None):
        limits = limits if limits is not None else Limits()
        return await collect_result(events(stream), limits=limits, selected_model="selected", request_id=request_id)

    async def test_result_uses_installed_framework_types_and_http_request_id(self):
        value = await self.collect([terminal(usage={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5})], request_id="req_http")
        self.assertIsInstance(value, LLMResult)
        self.assertIsInstance(value.usage, TokenUsage)
        self.assertEqual(value.message.to_dict(), {"role": "assistant", "content": "连接验证成功"})
        self.assertEqual(value.request_id, "req_http")
        self.assertEqual(value.usage.total_tokens, 5)
        self.assertEqual(value.finish_reason, "stop")
        missing = await self.collect([terminal()])
        self.assertIsNone(missing.usage)
        self.assertIsNone(missing.request_id)

    async def test_commentary_reasoning_and_deltas_never_become_final_answer(self):
        output = [message("internal", phase="commentary"), {"id": "reason", "type": "reasoning"},
                  message("可见正文", identifier="msg_final")]
        result = await self.collect([{"type": "response.output_text.delta", "delta": "partial"}, terminal(output=output)])
        self.assertEqual(result.message.content, "可见正文")

    async def test_done_items_restore_null_terminal_without_duplicate_deltas(self):
        end = terminal()
        end["response"]["output"] = None
        stream = [{"type": "response.output_text.delta", "delta": "partial"},
                  {"type": "response.output_item.done", "output_index": 2, "item": message("second", identifier="two")},
                  {"type": "response.output_item.done", "output_index": 1, "item": message("first", identifier="one")}, end]
        self.assertEqual((await self.collect(stream)).message.content, "first\nsecond")

    async def test_done_and_terminal_conflicts_are_rejected(self):
        done = {"type": "response.output_item.done", "output_index": 0, "item": message("first")}
        for bad in [terminal(output=[message("changed")]),
                    {"type": "response.output_item.done", "output_index": 0, "item": message("changed")},
                    {"type": "response.output_item.done", "output_index": 1, "item": message("first")}]:
            with self.subTest(bad=bad), self.assertRaises(ProviderError):
                await self.collect([done, bad, terminal()])

    async def test_tools_unknown_events_refusals_and_nonfinal_outputs_fail(self):
        for item in [{"type": "function_call"}, {"type": "web_search_call"}, {"type": "custom_tool_call"}]:
            for stream in [[terminal(output=[message(), item])],
                           [{"type": "response.output_item.added", "output_index": 0, "item": item}]]:
                with self.subTest(stream=stream), self.assertRaises(ProviderError):
                    await self.collect(stream)
        refusal = message()
        refusal["content"] = [{"type": "refusal", "refusal": "refused"}]
        for stream in [[terminal(output=[refusal])], [{"type": "response.refusal.delta"}],
                       [{"type": "unrecognized"}], [terminal(output=[message("internal", phase="commentary")])],
                       [terminal(output=[message(phase="unknown")])],
                       [terminal(output=[message(phase=[])])], [terminal(output=[{"type": []}])]]:
            with self.subTest(stream=stream), self.assertRaises(ProviderError):
                await self.collect(stream)

    async def test_only_explicit_completed_terminal_can_succeed(self):
        cases = [[], [{"type": "response.output_text.delta", "delta": "partial"}],
                 [terminal(status="in_progress")], [terminal(output=[])],
                 [{"type": "response.output_item.done", "output_index": 0, "item": message()}]]
        for failure in ["error", "response.failed", "response.incomplete", "response.cancelled"]:
            cases.append([{"type": failure}])
        for stream in cases:
            with self.subTest(stream=stream), self.assertRaises(ProviderError):
                await self.collect(stream)

    async def test_identity_and_malformed_terminal_usage_are_rejected(self):
        for malformed in [{}, "", False, 0]:
            end = terminal()
            end["response"]["output"] = malformed
            with self.subTest(malformed=malformed), self.assertRaises(ProviderError):
                await self.collect([end])
        for usage in [{}, {"input_tokens": True, "output_tokens": 1, "total_tokens": 2},
                      {"input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
                      {"input_tokens": 2, "output_tokens": 3, "total_tokens": 99}]:
            with self.subTest(usage=usage), self.assertRaises(ProviderError):
                await self.collect([terminal(usage=usage)])
        with self.assertRaises(ProviderError):
            await self.collect([{"type": "response.created", "response": {"id": "other"}}, terminal()])

    async def test_output_delta_item_and_event_bounds(self):
        cases = [([terminal()], replace(Limits(), output_chars=1)),
                 ([{"type": "response.output_text.delta", "delta": "xx"}], replace(Limits(), delta_chars=1)),
                 ([terminal(output=[message(), message(identifier="two")])], replace(Limits(), output_items=1)),
                 ([{"type": "response.output_text.delta", "delta": "x"}, terminal()], replace(Limits(), events=1))]
        for stream, limits in cases:
            with self.subTest(limits=limits), self.assertRaises(ProviderError) as caught:
                await self.collect(stream, limits)
            self.assertEqual(caught.exception.code, ErrorCode.LIMIT)
