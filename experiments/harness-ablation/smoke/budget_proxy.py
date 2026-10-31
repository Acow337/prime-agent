"""Budget accounting for the bounded DeepSeek smoke proxy."""

from __future__ import annotations

import http.client
import http.server
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_MAX_TRANSPORT_RETRIES = 2


class BudgetExceeded(RuntimeError):
    """Raised when a request violates a configured safety limit."""

    def __init__(self, category: str, status: int) -> None:
        super().__init__(category)
        self.category = category
        self.status = status


@dataclass(frozen=True)
class BudgetPolicy:
    input_usd_per_million: float
    output_usd_per_million: float
    context_tokens: int
    response_tokens: int
    max_provider_requests: int
    task_request_quota: Mapping[str, int]
    max_transport_retries: int
    usd_to_cny: float
    hard_budget_cny: float

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "BudgetPolicy":
        quota = manifest.get("task_request_quota") or {}
        if not isinstance(quota, Mapping):
            raise ValueError("task_request_quota must be a mapping of task id to limit")
        parsed_quota: dict[str, int] = {}
        for task_id, limit in quota.items():
            if not isinstance(task_id, str) or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError("task_request_quota values must be positive integers")
            parsed_quota[task_id] = limit
        return cls(
            input_usd_per_million=float(manifest["input_usd_per_million"]),
            output_usd_per_million=float(manifest["output_usd_per_million"]),
            context_tokens=int(manifest["context_tokens"]),
            response_tokens=int(manifest["response_tokens"]),
            max_provider_requests=int(manifest["max_provider_requests"]),
            task_request_quota=parsed_quota,
            max_transport_retries=int(
                manifest.get("max_transport_retries", DEFAULT_MAX_TRANSPORT_RETRIES)
            ),
            usd_to_cny=float(manifest["usd_to_cny"]),
            hard_budget_cny=float(manifest["hard_budget_cny"]),
        )

    def worst_case_request_cny(self) -> float:
        input_cost = self.context_tokens * self.input_usd_per_million / 1_000_000
        output_cost = self.response_tokens * self.output_usd_per_million / 1_000_000
        return (input_cost + output_cost) * self.usd_to_cny


class UsageLedger:
    """Thread-safe admission and sanitized provider-usage accounting."""

    def __init__(self, policy: BudgetPolicy) -> None:
        self._policy = policy
        self._lock = threading.Lock()
        self._request_count = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._rejection_count = 0
        self._error_categories: dict[str, int] = {}
        self._current_task: str | None = None
        self._task_request_counts: dict[str, int] = {}
        self._task_quota_override: int | None = None
        self._transport_retries = 0

    def begin_task(self, task_id: str, *, quota: int | None = None) -> None:
        """Scope admissions to one task, optionally with a run-specific quota."""
        with self._lock:
            self._current_task = task_id
            self._task_quota_override = quota

    def admit(self, payload: Mapping[str, Any]) -> None:
        token_fields = [
            payload[name]
            for name in ("max_tokens", "max_completion_tokens")
            if name in payload
        ]
        max_tokens = token_fields[0] if len(token_fields) == 1 else None
        with self._lock:
            if (
                not isinstance(max_tokens, int)
                or isinstance(max_tokens, bool)
                or max_tokens < 1
                or max_tokens > self._policy.response_tokens
            ):
                self._reject("response_token_limit", 400)
            if self._request_count >= self._policy.max_provider_requests:
                self._reject("request_limit", 402)
            task_used = 0
            if self._current_task is not None:
                task_used = self._task_request_counts.get(self._current_task, 0)
                task_quota = (
                    self._task_quota_override
                    if self._task_quota_override is not None
                    else self._policy.task_request_quota.get(self._current_task)
                )
                if task_quota is not None and task_used >= task_quota:
                    self._reject("task_request_limit", 402)
            projected_cost = (
                self._request_count + self._transport_retries + 1
            ) * self._policy.worst_case_request_cny()
            if projected_cost > self._policy.hard_budget_cny:
                self._reject("budget_limit", 402)
            self._request_count += 1
            if self._current_task is not None:
                self._task_request_counts[self._current_task] = task_used + 1

    def record_transport_retry(self) -> None:
        """Count one re-sent request whose first attempt never answered."""
        with self._lock:
            self._transport_retries += 1

    def _reject(self, category: str, status: int) -> None:
        self._rejection_count += 1
        self._error_categories[category] = self._error_categories.get(category, 0) + 1
        raise BudgetExceeded(category, status)

    def record_usage(self, usage: Mapping[str, Any]) -> None:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
            with self._lock:
                self._error_categories["invalid_usage"] = (
                    self._error_categories.get("invalid_usage", 0) + 1
                )
            return
        with self._lock:
            self._prompt_tokens += max(prompt_tokens, 0)
            self._completion_tokens += max(completion_tokens, 0)

    def record_error(self, category: str) -> None:
        with self._lock:
            self._error_categories[category] = self._error_categories.get(category, 0) + 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            actual_cost_usd = (
                self._prompt_tokens * self._policy.input_usd_per_million
                + self._completion_tokens * self._policy.output_usd_per_million
            ) / 1_000_000
            return {
                "request_count": self._request_count,
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "actual_cost_usd": round(actual_cost_usd, 12),
                "actual_cost_cny": round(
                    actual_cost_usd * self._policy.usd_to_cny, 12
                ),
                "conservative_cost_cny": round(
                    (self._request_count + self._transport_retries)
                    * self._policy.worst_case_request_cny(),
                    12,
                ),
                "rejection_count": self._rejection_count,
                "transport_retries": self._transport_retries,
                "task_request_counts": dict(sorted(self._task_request_counts.items())),
                "error_categories": dict(sorted(self._error_categories.items())),
            }


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers with credentials removed case-insensitively."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in {"authorization", "proxy-authorization"}
    }


class _SseUsageCapture:
    def __init__(self) -> None:
        self._buffer = b""
        self.usage: Mapping[str, Any] | None = None

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            self._parse_line(line.rstrip(b"\r"))

    def finish(self) -> None:
        if self._buffer:
            self._parse_line(self._buffer.rstrip(b"\r"))
            self._buffer = b""

    def _parse_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        data = line.removeprefix(b"data:").strip()
        if not data or data == b"[DONE]":
            return
        try:
            event = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(event, dict) and isinstance(event.get("usage"), dict):
            self.usage = event["usage"]


def open_upstream(
    ledger: UsageLedger,
    request: urllib.request.Request,
    *,
    timeout: float,
    max_retries: int,
    opener: Any = urllib.request.urlopen,
    sleep: Any = time.sleep,
) -> Any:
    """Open the upstream request, re-sending only transport-level failures.

    An HTTP error is a provider answer and is never retried here. A connect or
    read failure that produced no response is retried a bounded number of
    times; each re-send is charged to the conservative cost bound so a lost
    first response cannot silently double the spend.
    """
    attempt = 0
    while True:
        try:
            return opener(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError):
            if attempt >= max_retries:
                raise
            ledger.record_transport_retry()
            attempt += 1
            sleep(0.5 * attempt)


class _ProxyServer(http.server.ThreadingHTTPServer):
    policy: BudgetPolicy
    ledger: UsageLedger
    upstream: str


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    max_request_bytes = 16 * 1024 * 1024

    @property
    def proxy_server(self) -> _ProxyServer:
        assert isinstance(self.server, _ProxyServer)
        return self.server

    def do_POST(self) -> None:
        if self.path != "/chat/completions":
            self._send_error(404, "unsupported_path")
            return
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "")
        except ValueError:
            self._send_error(400, "invalid_content_length")
            return
        if length < 1 or length > self.max_request_bytes:
            self._send_error(400, "invalid_content_length")
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error(400, "invalid_json")
            return
        if not isinstance(payload, dict):
            self._send_error(400, "invalid_json")
            return
        try:
            self.proxy_server.ledger.admit(payload)
        except BudgetExceeded as error:
            self._send_error(error.status, error.category)
            return
        headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "text/event-stream"),
        }
        authorization = self.headers.get("Authorization")
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(
            self.proxy_server.upstream,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            response = open_upstream(
                self.proxy_server.ledger,
                request,
                timeout=180,
                max_retries=self.proxy_server.policy.max_transport_retries,
            )
            with response:
                self._forward(response)
        except urllib.error.HTTPError as error:
            category = f"upstream_http_{error.code}"
            self.proxy_server.ledger.record_error(category)
            with error:
                self._forward(error)
        except (OSError, urllib.error.URLError):
            self.proxy_server.ledger.record_error("upstream_connection")
            self._send_error(502, "upstream_connection")

    def _forward(self, response: Any) -> None:
        self.send_response(response.status)
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        self.send_header("Content-Type", content_type)
        self.send_header("Connection", "close")
        self.end_headers()
        capture = _SseUsageCapture()
        while True:
            try:
                chunk = response.read(64 * 1024)
            except http.client.HTTPException:
                # A truncated chunked body is an upstream transport failure;
                # record it instead of letting the handler thread crash.
                self.proxy_server.ledger.record_error("upstream_incomplete")
                break
            if not chunk:
                break
            self.wfile.write(chunk)
            if content_type.lower().startswith("text/event-stream"):
                capture.feed(chunk)
        capture.finish()
        if capture.usage is not None:
            self.proxy_server.ledger.record_usage(capture.usage)
        elif (
            200 <= response.status < 300
            and content_type.lower().startswith("text/event-stream")
        ):
            self.proxy_server.ledger.record_error("missing_usage")
        self.close_connection = True

    def _send_error(self, status: int, category: str) -> None:
        body = json.dumps(
            {"error": {"type": "bounded_proxy", "category": category}},
            separators=(",", ":"),
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, _format: str, *_args: Any) -> None:
        pass


@dataclass
class ProxyHandle:
    base_url: str
    ledger: UsageLedger
    _server: _ProxyServer
    _thread: threading.Thread

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


def start_proxy(
    policy: BudgetPolicy,
    upstream: str | None = None,
) -> ProxyHandle:
    """Start a loopback-only proxy with a pre-forward admission gate."""
    server = _ProxyServer(("127.0.0.1", 0), _ProxyHandler)
    server.policy = policy
    server.ledger = UsageLedger(policy)
    server.upstream = upstream or "https://api.deepseek.com/chat/completions"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return ProxyHandle(
        base_url=f"http://127.0.0.1:{server.server_port}",
        ledger=server.ledger,
        _server=server,
        _thread=thread,
    )
