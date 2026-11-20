# Hermes subscription research

This is an isolated research directory, not a production provider or Agent runtime.

- `fetch_sources.py`: downloads selected public files from one pinned Hermes commit; verifies Git blob IDs and writes SHA-256 records. It never installs or imports upstream code.
- `sources/<commit>/`: static `.source.txt` snapshots, including the upstream MIT license and a manifest. Upstream tests are text only and were not executed.
- `contract_probe.py`: original, text-only request/result contract experiment. No HTTP client, OAuth login, token store, or environment loading.
- `test_contract_probe.py`: synthetic-event tests using the existing AI_agent `llm` package and its result models. Its process-local import path is for research only.

Read [the research report](../../docs/HERMES_CODEX_RESEARCH.md) for findings and limitations.

From `D:\qieehy\WeChatAgent`:

```powershell
& 'D:\qieehy\AI_agent\.venv\Scripts\python.exe' -B -m unittest discover -s research\hermes_subscription -p test_contract_probe.py -v
```

Windows asyncio creates a local socketpair to initialize its event loop. A sandbox that blocks it may stall before an asynchronous test begins. The completed run used an environment permitting that local mechanism; the tests do not contact an external service.

Final result on 2026-10-26: **19 tests passed**. This verifies synthetic protocol behavior and framework result types, not a real subscription connection, credential refresh, raw SSE parser, complete Runtime integration, or WeChat behavior.
