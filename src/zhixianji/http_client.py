"""只读 HTTP 客户端：超时、有限重试、退避、遵守 Retry-After。

只发送 GET。不携带 Cookie、不保存整套请求头，不做任何写操作。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class FetchError(RuntimeError):
    """重试耗尽后仍然失败。"""

    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{url} -> {message}")
        self.url = url
        self.status = status


def _decode(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class HttpClient:
    """串行、礼貌的 GET 客户端。"""

    def __init__(
        self,
        user_agent: str,
        timeout_ms: int = 20000,
        max_retries: int = 3,
        delay_ms: int = 0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = max(1.0, timeout_ms / 1000)
        self.max_retries = max(0, max_retries)
        self.delay = max(0.0, delay_ms / 1000)
        self._last_request_at = 0.0
        self.request_count = 0

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str, accept: str = "application/json") -> Response:
        """GET url。4xx（除 429）不重试，直接返回；5xx 与网络错误有限重试。"""
        last_error: str = "未知错误"
        last_status: int | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": self.user_agent, "Accept": accept},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    body = resp.read()
                    self._last_request_at = time.monotonic()
                    self.request_count += 1
                    return Response(
                        url=url,
                        status=resp.status,
                        text=_decode(body, resp.headers.get("Content-Type", "")),
                        headers={k.lower(): v for k, v in resp.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                self.request_count += 1
                last_status = exc.code
                body = b""
                try:
                    body = exc.read()
                except Exception:  # noqa: BLE001 - 读取错误响应体失败不应掩盖状态码
                    body = b""
                if exc.code == 429 or 500 <= exc.code < 600:
                    last_error = f"HTTP {exc.code}"
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    self._sleep_before_retry(attempt, retry_after)
                    continue
                # 其余 4xx 视为确定性结果（例如 404 已下线），交给调用方判断。
                return Response(
                    url=url,
                    status=exc.code,
                    text=_decode(body, exc.headers.get("Content-Type", "") if exc.headers else ""),
                    headers={k.lower(): v for k, v in (exc.headers or {}).items()},
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self._last_request_at = time.monotonic()
                last_error = f"{type(exc).__name__}: {exc}"
                self._sleep_before_retry(attempt, None)

        raise FetchError(url, last_error, last_status)

    def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self.max_retries:
            return
        delay = min(30.0, 1.5 * (2**attempt))
        if retry_after:
            try:
                delay = max(delay, min(60.0, float(retry_after)))
            except ValueError:
                pass
        time.sleep(delay)
