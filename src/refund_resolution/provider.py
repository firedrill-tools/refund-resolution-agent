"""Bounded native Anthropic transport; capture before SDK validation/defaults."""
import hashlib
import json
from datetime import datetime, timezone

import httpx2
from anthropic import AsyncAnthropic
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

MAX_BODY = 1_048_576
COUNTERS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def reported_usage(body):
    raw = body.get("usage") if isinstance(body, dict) else None
    values = {key: raw.get(key) if isinstance(raw, dict) and type(raw.get(key)) is int and 0 <= raw[key] <= 2**53-1 else None for key in COUNTERS}
    complete = all(value is not None for value in values.values())
    input_tokens = sum(values[key] for key in (COUNTERS[0], COUNTERS[2], COUNTERS[3])) if complete else None
    total = input_tokens + values["output_tokens"] if complete else None
    complete = complete and total <= 2**53-1
    return {"fields": values, "complete": complete, "input_tokens": input_tokens if complete else None,
            "output_tokens": values["output_tokens"] if complete else None,
            "total_tokens": total if complete else None, "cost_usd": None}


def aggregate_usage(observations):
    measured = [o.get("reported_usage") for o in observations]
    complete = bool(measured) and all(u and u["complete"] for u in measured)
    fields = {key: sum(u["fields"][key] for u in measured) if measured and all(u and u["fields"][key] is not None for u in measured) else None for key in COUNTERS}
    totals = {key: sum(u[key] for u in measured) if complete else None for key in ("input_tokens", "output_tokens", "total_tokens")}
    if any(value is not None and value > 2**53-1 for value in (*fields.values(), *totals.values())):
        complete = False
        totals = dict.fromkeys(totals)
        fields = {key: value if value is None or value <= 2**53-1 else None for key, value in fields.items()}
    return {"attempts": len(observations), "measured_attempts": sum(bool(u and u["complete"]) for u in measured),
            "complete": complete, "fields": fields, **totals, "cost_usd": None}


class ProviderEvidence:
    def __init__(self, callback=None):
        self.observations = []
        self.callback = callback

    def publish(self):
        if self.callback:
            self.callback(self.observations)

    async def request(self, request):
        raw = await request.aread()
        body = json.loads(raw)
        self.observations.append({"attempt": len(self.observations) + 1, "status": "started", "started_at": now(),
            "finished_at": None, "input_sha256": digest(raw), "requested_model": body.get("model"),
            "limits": {key: body.get(key) for key in ("max_tokens", "stream")}, "http_status": None,
            "request_id": None, "response_model": None, "finish_reason": None, "body": None,
            "reported_usage": None, "error": None})
        self.publish()

    async def response(self, response):
        observation = self.observations[-1]
        observation.update(http_status=response.status_code, request_id=response.headers.get("request-id"), finished_at=now(), status="received")
        try:
            # Public aread buffers bytes for the real SDK as well. The evidence
            # cap is checked after HTTP buffering; it is not a wire/memory bound.
            raw = await response.aread()
            observation["body_sha256"] = digest(bytes(raw))
            observation["truncated"] = len(raw) > MAX_BODY
            text = bytes(raw[:MAX_BODY]).decode("utf8", errors="replace")
            try:
                body = json.loads(text)
            except ValueError:
                body = text
            observation["body"] = body
            observation["body_source"] = "HTTP decoded response bytes before SDK validation"
            observation["reported_usage"] = reported_usage(body)
            if isinstance(body, dict):
                observation.update(response_model=body.get("model"), finish_reason=body.get("stop_reason"), response_id=body.get("id"))
            self.publish()
            if observation["truncated"]:
                raise ValueError("provider response exceeds 1 MiB capture limit")
            if 200 <= response.status_code < 300:
                validate_completion(body)
                observation["status"] = "accepted"
            else:
                observation["status"] = "http_error"
            self.publish()
        except BaseException as error:
            observation.update(status="rejected", error={"name": type(error).__name__, "message": str(error)})
            self.publish()
            raise
        finally:
            await response.aclose()

    def fail(self, error):
        if self.observations and self.observations[-1]["status"] == "started":
            self.observations[-1].update(status="failed", finished_at=now(), error={"name": type(error).__name__, "message": str(error)})
            self.publish()


def validate_completion(body):
    if not isinstance(body, dict) or body.get("type") != "message" or body.get("role") != "assistant" or not isinstance(body.get("id"), str) or not body["id"].strip():
        raise ValueError("provider did not return a completed message")
    content = body.get("content")
    if not isinstance(content, list) or not content or not all(isinstance(part, dict) for part in content):
        raise ValueError("provider returned empty or malformed content")
    calls = [part for part in content if part.get("type") == "tool_use"]
    reason = body.get("stop_reason")
    if reason == "tool_use":
        if not calls or any(not isinstance(p.get("id"), str) or not p["id"].strip() or not isinstance(p.get("name"), str) or not p["name"].strip() or not isinstance(p.get("input"), dict) for p in calls):
            raise ValueError("provider tool completion lacks valid call identity/arguments")
        if len({p["id"] for p in calls}) != len(calls):
            raise ValueError("provider repeated a tool call identity")
        if any(p["name"] == "refund_proposal" for p in calls) and (len(calls) != 1 or calls[0]["name"] != "refund_proposal"):
            raise ValueError("proposal cannot be mixed with read tools")
    elif reason == "end_turn" and not calls and any(p.get("type") == "text" and isinstance(p.get("text"), str) and p["text"].strip() for p in content):
        pass
    else:
        raise ValueError(f"provider response is unfinished or inconsistent ({reason!r})")


def create_client(key, evidence, *, transport=None, base_url="https://api.anthropic.com"):
    # Owned client, no SDK retry; endpoint is explicit, never inherited from a
    # provider-global base-url variable. The transport seam retains the native SDK.
    http = httpx2.AsyncClient(transport=transport, timeout=httpx2.Timeout(30, connect=5),
        event_hooks={"request": [evidence.request], "response": [evidence.response]})
    return AsyncAnthropic(api_key=key, base_url=base_url, max_retries=0, timeout=30, http_client=http)


def native_model(name, client):
    return AnthropicModel(name, provider=AnthropicProvider(anthropic_client=client))
