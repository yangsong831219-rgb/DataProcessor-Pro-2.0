"""ai_errors.py — AI 客户端类型化异常层级

阶段一 (P0)：AIClient.generate() 不再吞异常返回空串，改为按 HTTP 语义
抛出对应异常子类。调用方可据此决定是否重试。
"""

from __future__ import annotations

from typing import Optional


class AIClientError(Exception):
    """AI 客户端异常基类。

    Attributes:
        message:      用户可读错误描述
        status_code:  HTTP 状态码（非 HTTP 错误为 None）
        cause:        原始异常（如有）
        retryable:    是否适合自动重试（子类覆盖）
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.cause = cause

    @property
    def retryable(self) -> bool:
        return False


class AIClientAuthError(AIClientError):
    """认证/授权错误 (401, 403) — 不可重试，需用户修复凭证。"""

    @property
    def retryable(self) -> bool:
        return False


class AIClientRequestError(AIClientError):
    """请求参数错误 (400, 422) — 不可重试，需修复请求内容。"""

    @property
    def retryable(self) -> bool:
        return False


class AIClientRateLimitError(AIClientError):
    """速率限制 (429) — 可重试。"""

    @property
    def retryable(self) -> bool:
        return True


class AIClientServerError(AIClientError):
    """服务端错误 (5xx) — 可重试。"""

    @property
    def retryable(self) -> bool:
        return True


class AIClientTimeoutError(AIClientError):
    """超时 / 连接失败 — 可重试。"""

    @property
    def retryable(self) -> bool:
        return True


class AIClientEmptyResponseError(AIClientError):
    """API 返回 200 但 content 为空 — 不可重试，模型可能不支持该请求。"""

    @property
    def retryable(self) -> bool:
        return False


class AIClientNotConfiguredError(AIClientError):
    """API Key 未配置 — 不可重试。"""

    @property
    def retryable(self) -> bool:
        return False


# ── 错误分类工厂 ──


def classify_openai_error(exc: Exception) -> AIClientError:
    """将 openai SDK 异常或 httpx 异常映射到对应 AIClientError 子类。

    基于 HTTP 状态码和异常类型进行精确分类。
    """
    msg = str(exc)
    status_code: int | None = None

    # --- 尝试从 openai SDK 异常中提取 HTTP 状态码 ---
    if hasattr(exc, "status_code"):
        status_code = getattr(exc, "status_code")
    elif hasattr(exc, "response") and hasattr(getattr(exc, "response"), "status_code"):
        status_code = getattr(exc, "response").status_code
    elif hasattr(exc, "__cause__") and exc.__cause__ is not None:
        # httpx 异常可能是 openai SDK 的 __cause__
        inner = exc.__cause__
        if hasattr(inner, "status_code"):
            status_code = inner.status_code
        elif hasattr(inner, "response") and hasattr(getattr(inner, "response"), "status_code"):
            status_code = getattr(inner, "response").status_code

    # --- 按 HTTP 状态码分类 ---
    if status_code is not None:
        if status_code in (401, 403):
            return AIClientAuthError(
                f"API 认证失败 (HTTP {status_code}): {msg}",
                status_code=status_code,
                cause=exc,
            )
        if status_code in (400, 422):
            return AIClientRequestError(
                f"请求参数错误 (HTTP {status_code}): {msg}",
                status_code=status_code,
                cause=exc,
            )
        if status_code == 429:
            return AIClientRateLimitError(
                f"API 速率限制 (HTTP 429): {msg}",
                status_code=429,
                cause=exc,
            )
        if 500 <= status_code < 600:
            return AIClientServerError(
                f"API 服务器错误 (HTTP {status_code}): {msg}",
                status_code=status_code,
                cause=exc,
            )

    # --- 按异常类型/消息分类（无状态码的分支） ---
    msg_lower = msg.lower()
    if "timeout" in msg_lower or "timed out" in msg_lower or "connection" in msg_lower:
        return AIClientTimeoutError(
            f"API 连接超时: {msg}",
            cause=exc,
        )
    if "401" in msg or "403" in msg or "unauthorized" in msg_lower or "invalid api key" in msg_lower:
        return AIClientAuthError(
            f"API 认证失败: {msg}",
            cause=exc,
        )
    if "429" in msg or "rate_limit" in msg_lower or "rate limit" in msg_lower:
        return AIClientRateLimitError(
            f"API 速率限制: {msg}",
            cause=exc,
        )
    if "500" in msg or "502" in msg or "503" in msg or "server error" in msg_lower:
        return AIClientServerError(
            f"API 服务器错误: {msg}",
            cause=exc,
        )

    # --- 兜底：归类为服务器错误（可重试） ---
    return AIClientServerError(
        f"AI 调用失败: {msg}",
        cause=exc,
    )
