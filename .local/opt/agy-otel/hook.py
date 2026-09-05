#!/usr/bin/env python3
"""
~/.local/opt/agy-otel/hook.py.

Antigravity CLI hook -> OpenTelemetry span exporter.

Registered per event in ~/.gemini/config/hooks.json. The event name is passed as
argv[1] because Antigravity's hook payloads carry no event-type field.

Only passive events are supported: PostToolUse, PostInvocation and Stop. PreToolUse
is deliberately excluded because its `decision` field is required and every legal
value changes permission behaviour, so a telemetry hook cannot observe it neutrally.
"""

import json, os, sys, time, hashlib

PASSIVE_OUTPUT = {"PostToolUse": {}, "PostInvocation": {}, "Stop": {}}

CONFIG_PATH = os.path.expanduser("~/.config/agy-otel/env")

def load_config():
    """
    Read OTLP settings from a config file.

    Interactive `agy` sessions inherit whatever shell they were launched from, so
    hooks cannot rely on exported env vars. Without this the SDK silently falls
    back to localhost:4318 and every span is dropped with connection refused.
    Real environment variables still win.
    """
    try:
        with open(CONFIG_PATH) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()
    except FileNotFoundError:
        pass

QUOTA_STAMP = os.path.expanduser("~/.config/agy-otel/.quota-last")
QUOTA_MIN_INTERVAL = int(os.environ.get("AGY_OTEL_QUOTA_INTERVAL", "300"))

def _quota_due():
    """Rate-limit quota sampling: /usage costs ~5s and the value moves slowly."""
    try:
        if time.time() - os.path.getmtime(QUOTA_STAMP) < QUOTA_MIN_INTERVAL:
            return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(QUOTA_STAMP), exist_ok=True)
        open(QUOTA_STAMP, "w").close()
    except OSError:
        pass
    return True

def emit_quota(meter):
    """
    Sample `agy -p /usage` and record remaining quota per bucket.

    Antigravity is a flat-rate subscription and never exposes per-token cost to
    interactive sessions, so quota consumed is the usable spend signal. The
    command starts no agent turn, spends no quota and fires no hooks, so it is
    safe to call from inside a hook.
    """
    import json as _json
    import subprocess

    out = subprocess.run(["agy", "-p", "/usage", "--output-format", "json"],
                         capture_output=True, text=True, timeout=60).stdout

    data = _json.loads(out, strict=False).get("command", {}).get("data", {})

    remaining = meter.create_gauge(
        "agy.quota.remaining_fraction", unit="1",
        description="Fraction of the Antigravity weekly quota still available")

    reset_in = meter.create_gauge(
        "agy.quota.seconds_to_reset", unit="s",
        description="Seconds until the Antigravity quota window resets")

    now = time.time()

    for group in data.get("groups", []):
        for bucket in group.get("buckets", []):
            attrs = {
                "agy.quota.group": group.get("name", ""),
                "agy.quota.bucket": bucket.get("id", ""),
                "agy.quota.window": bucket.get("window", ""),
            }

            frac = bucket.get("remaining_fraction")

            if frac is not None:
                remaining.set(float(frac), attrs)

            reset = bucket.get("reset_time")

            if reset:
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                    reset_in.set(max(0.0, ts.timestamp() - now), attrs)
                except Exception:
                    pass

def emit(event, payload):
    # Imported here, not at module scope: the OpenTelemetry SDK costs ~200ms to
    # import and only the detached child needs it, so the agent loop never waits.
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.trace import (SpanKind, SpanContext, TraceFlags,
                                     NonRecordingSpan, set_span_in_context)
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")

    conv = payload.get("conversationId") or "unknown"

    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "antigravity-cli"),
    })

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    tracer = provider.get_tracer("antigravity-cli-hooks")

    # Hooks are separate processes, so group a conversation's spans by deriving a
    # stable trace id from conversationId. The root span is never emitted, so the
    # trace shows a missing-root placeholder; that is expected.
    digest = hashlib.sha256(conv.encode()).hexdigest()

    ctx = set_span_in_context(NonRecordingSpan(SpanContext(
        trace_id=int(digest[:32], 16),
        span_id=int(digest[32:48], 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )))

    tool = payload.get("toolName") or (payload.get("toolCall") or {}).get("name")

    if event == "PostToolUse":
        name = f"execute_tool {tool or 'unknown'}"
        op = "execute_tool"
    elif event == "Stop":
        name, op = "agy stop", "invoke_agent"
    else:
        name, op = "agy invocation", "invoke_agent"

    now = time.time()
    span = tracer.start_span(name, context=ctx, kind=SpanKind.INTERNAL,
                             start_time=int(now * 1e9))
    span.set_attribute("gen_ai.operation.name", op)
    span.set_attribute("gen_ai.provider.name", "gcp.gemini")
    span.set_attribute("gen_ai.conversation.id", conv)
    span.set_attribute("agy.hook.event", event)

    if payload.get("modelName"):
        span.set_attribute("gen_ai.request.model", payload["modelName"])

    for key, attr in (("stepIdx", "agy.step.index"),
                      ("invocationNum", "agy.invocation.num"),
                      ("initialNumSteps", "agy.initial_num_steps"),
                      ("executionNum", "agy.execution.num"),
                      ("terminationReason", "agy.termination_reason"),
                      ("fullyIdle", "agy.fully_idle")):
        if payload.get(key) is not None:
            span.set_attribute(attr, payload[key])

    if tool:
        span.set_attribute("gen_ai.tool.name", tool)

    err = payload.get("error")

    if err:
        span.set_attribute("error.type", str(err)[:200])
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(err)[:200]))
    span.end(end_time=int(now * 1e9))

    provider.force_flush(2000)
    provider.shutdown()

    # Quota is the only spend signal available to interactive sessions.
    if event == "Stop" and _quota_due():
        try:
            mp = MeterProvider(resource=resource, metric_readers=[
                PeriodicExportingMetricReader(OTLPMetricExporter(), 60000)])
            emit_quota(mp.get_meter("antigravity-cli-quota"))
            mp.force_flush(5000)
            mp.shutdown()
        except Exception:
            pass

def main():
    load_config()

    event = sys.argv[1] if len(sys.argv) > 1 else "PostInvocation"

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    # Antigravity runs hooks synchronously and blocks the agent loop, and a
    # foreground OTLP export costs 0.5-1s per tool call. Fork so the parent can
    # answer immediately and the child ships the span in the background.
    try:
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                try:
                    emit(event, payload)
                finally:
                    os._exit(0)
            os._exit(0)
        else:
            os.wait()  # reap the short-lived intermediate child
    except Exception:
        pass  # never break the agent loop on a telemetry failure

    # Passive response: must be valid JSON and must not alter agent behaviour.
    print(json.dumps(PASSIVE_OUTPUT.get(event, {})))

if __name__ == "__main__":
    main()
