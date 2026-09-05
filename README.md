# Antigravity CLI OTEL Traces

Zero-overhead OpenTelemetry (OTel) instrumentation for Google Antigravity CLI (`agy`) sessions via hooks.

Exports distributed traces (tool calls, agent invocations, stops) and quota usage metrics directly to any standard OTLP endpoint (Jaeger, Grafana Tempo, OpenTelemetry Collector, Datadog, etc.).

---

## Highlights

- **Zero Agent Latency**: Antigravity runs hooks synchronously. Emitting an OTLP span over HTTP takes ~0.5–1.0s. `hook.py` uses a double-fork daemon process to ship spans in the background, keeping hook response time to under 1ms.
- **Deterministic Conversation Traces**: Automatically groups an entire CLI conversation into a single distributed trace by hashing `conversationId` into a stable W3C `trace_id`.
- **Passive & Non-Destructive**: Observes `PostToolUse`, `PostInvocation`, and `Stop` events with strictly valid empty responses (`{}`). PreToolUse is intentionally avoided so telemetry never mutates permissions or blocks tool executions.
- **Quota Tracking**: Queries `agy -p /usage` on agent shutdown (throttled to every 5 minutes by default) to export quota remaining fractions and reset countdowns without consuming agent turns or quota.
- **Safe Hook Merging**: `install.sh` non-destructively merges the hook configuration into `~/.gemini/config/hooks.json`, preserving existing hooks (e.g. `orca-status`).

---

## Quick Start

### 1. Install

Clone the repository and run the installer:

```bash
git clone https://github.com/<owner>/antigravity-otel.git
cd antigravity-otel
./install.sh
```

What `install.sh` does:
1. Creates an isolated Python virtualenv at `~/.local/opt/agy-otel`.
2. Installs `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`.
3. Copies `hook.py` to `~/.local/opt/agy-otel/hook.py`.
4. Initializes `~/.config/agy-otel/env` (with `0600` permissions) if not already present.
5. Safely registers `agy-otel` in `~/.gemini/config/hooks.json`.

### 2. Configure Endpoint

Edit `~/.config/agy-otel/env` with your OTLP backend settings:

#### Local Collector / Jaeger (Default HTTP 4318)
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_SERVICE_NAME=antigravity-cli
```

> **Note**: Interactive `agy` sessions inherit the shell they were started in, so `~/.config/agy-otel/env` guarantees endpoints are loaded even if environment variables are not globally exported in every subshell. Real environment variables still take precedence.

---

## Telemetry Details

### Spans

| Hook Event | Span Name | Span Kind | Attributes |
|---|---|---|---|
| `PostToolUse` | `execute_tool <tool_name>` | `INTERNAL` | `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `agy.step.index`, `error.type` |
| `PostInvocation` | `agy invocation` | `INTERNAL` | `gen_ai.operation.name=invoke_agent`, `agy.invocation.num`, `gen_ai.request.model` |
| `Stop` | `agy stop` | `INTERNAL` | `gen_ai.operation.name=invoke_agent`, `agy.termination_reason`, `agy.fully_idle` |

Every span carries `gen_ai.provider.name="gcp.gemini"`, `gen_ai.conversation.id`, and `agy.hook.event`.

### Metrics

Emitted during the `Stop` event when rate-limit window expires (`AGY_OTEL_QUOTA_INTERVAL`, default: 300s):

| Metric | Type | Unit | Description |
|---|---|---|---|
| `agy.quota.remaining_fraction` | Gauge | `1` | Available fraction (0.0 to 1.0) of weekly quota |
| `agy.quota.seconds_to_reset` | Gauge | `s` | Seconds until quota window reset |

---

## Testing

An integration test suite is provided in [Dockerfile.test](file:///Users/anthuanvasquez/Sites/antigravity-otel/Dockerfile.test). It verifies:
- Fresh installation via `./install.sh`.
- Virtual environment and binary permissions (`0600` on env, `+x` on hook).
- Non-destructive merging into `~/.gemini/config/hooks.json`.
- Mock hook payload execution for `PostToolUse`, `PostInvocation`, and `Stop`.

To run the tests:

```bash
docker build -f Dockerfile.test .
```

---

## Continuous Integration

GitHub Actions runs on every push and pull request to `main` via [.github/workflows/ci.yml](file:///.github/workflows/ci.yml):
1. **Syntax & Lint**: Verifies bash scripts (`bash -n`) and Python compilation (`py_compile`).
2. **Integration Test**: Builds and executes the end-to-end test suite inside the test container.

---

## License
MIT License © 2026 Anthuan Vásquez. See [LICENSE](LICENSE) for details.
