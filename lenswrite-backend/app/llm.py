"""Shared LLM factory with optional HTTP proxy for outbound xAI calls."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# Load backend .env early (local dev). Cloud platforms inject env vars directly.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=False)

DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1,0.0.0.0"


def _explicit_proxy() -> str | None:
    """Return proxy URL only when explicitly configured.

    Production (Railway/Render/Fly) should NOT use a local VPN proxy.
    Local dev: set VPN_HTTP_PROXY or HTTPS_PROXY in .env if needed.
    """
    # Explicit disable
    flag = (os.getenv("USE_HTTP_PROXY") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None

    proxy = (
        os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("http_proxy")
        or os.getenv("VPN_HTTP_PROXY")
    )
    proxy = (proxy or "").strip() or None

    # If USE_HTTP_PROXY=1 but no URL, do not invent a localhost default in cloud.
    return proxy


def apply_proxy_env() -> str | None:
    """Apply outbound proxy env vars when configured; otherwise leave unset."""
    proxy = _explicit_proxy()
    no_proxy = os.getenv("NO_PROXY") or os.getenv("no_proxy") or DEFAULT_NO_PROXY

    for host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        if host not in no_proxy.split(","):
            no_proxy = f"{no_proxy},{host}" if no_proxy else host

    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy
    else:
        # Avoid inheriting a broken local proxy from the shell in production workers.
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            # Only clear if it points at common local VPN ports and USE_HTTP_PROXY is off
            val = os.environ.get(k, "")
            if "127.0.0.1:4780" in val or "localhost:4780" in val:
                os.environ.pop(k, None)

    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy
    return proxy


apply_proxy_env()


def get_proxy_url() -> str | None:
    return apply_proxy_env()


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=30.0)


@lru_cache(maxsize=8)
def _clients(timeout_seconds: int) -> tuple[httpx.Client, httpx.AsyncClient]:
    proxy = get_proxy_url()
    timeout = _timeout(float(timeout_seconds))
    # httpx: proxy=None means direct outbound (correct for cloud)
    sync = httpx.Client(proxy=proxy, timeout=timeout)
    async_client = httpx.AsyncClient(proxy=proxy, timeout=timeout)
    return sync, async_client


def make_chat_openai(
    *,
    model: str | None = None,
    temperature: float = 0.7,
    timeout: int = 120,
    max_retries: int = 2,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """ChatOpenAI pointed at xAI; optional proxy for local VPN only."""
    apply_proxy_env()
    http_client, http_async_client = _clients(int(timeout))

    # Avoid langchain-openai installing a custom transport that drops proxy settings.
    os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is not set. Configure it in the environment before starting the API."
        )

    kwargs: dict = {
        "model": model or os.getenv("XAI_MODEL", "grok-3"),
        "temperature": temperature,
        "api_key": api_key,
        "base_url": os.getenv("XAI_BASE_URL", "https://api.x.ai/v1"),
        "timeout": timeout,
        "max_retries": max_retries,
        "http_client": http_client,
        "http_async_client": http_async_client,
        "http_socket_options": (),
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    try:
        return ChatOpenAI(**kwargs)
    except TypeError:
        kwargs.pop("http_socket_options", None)
        return ChatOpenAI(**kwargs)
