from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(slots=True)
class StepResult:
    name: str
    ok: bool
    message: str
    elapsed_ms: int
    status_code: int | None = None
    body_preview: str | None = None


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _print_result(result: StepResult) -> None:
    tag = "PASS" if result.ok else "FAIL"
    status = f" status={result.status_code}" if result.status_code is not None else ""
    print(f"[{tag}] {result.name} ({result.elapsed_ms}ms){status} -> {result.message}")
    if result.body_preview:
        print(f"      body: {result.body_preview}")


def _trim(text: str, limit: int = 300) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    timeout: float,
    payload: dict[str, object] | None = None,
    force_ipv4: bool = False,
) -> StepResult:
    started = time.perf_counter()
    transport = httpx.HTTPTransport(local_address="0.0.0.0") if force_ipv4 else None
    step_name = f"{method} {url}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, transport=transport) as client:
            response = client.request(method, url, headers=headers, json=payload)
        elapsed = int((time.perf_counter() - started) * 1000)
        text = _trim(response.text)
        ok = response.status_code < 500
        return StepResult(
            name=step_name,
            ok=ok,
            message="ok" if ok else "server_error",
            elapsed_ms=elapsed,
            status_code=response.status_code,
            body_preview=text,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return StepResult(
            name=step_name,
            ok=False,
            message=f"{exc.__class__.__name__}: {exc}",
            elapsed_ms=elapsed,
        )


def _resolve_dns(base_url: str) -> StepResult:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    started = time.perf_counter()
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({item[4][0] for item in records})
        elapsed = int((time.perf_counter() - started) * 1000)
        return StepResult(
            name=f"DNS {host}",
            ok=True,
            message=f"resolved {len(ips)} IP(s)",
            elapsed_ms=elapsed,
            body_preview=", ".join(ips[:6]),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.perf_counter() - started) * 1000)
        return StepResult(
            name=f"DNS {host}",
            ok=False,
            message=f"{exc.__class__.__name__}: {exc}",
            elapsed_ms=elapsed,
        )


def run_diagnostics(
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider_type: str,
    backend_url: str,
    timeout: float,
) -> int:
    print("== Provider Connectivity Diagnostics ==")
    print(f"provider_type={provider_type}")
    print(f"base_url={base_url}")
    print(f"model={model}")
    print(f"api_key={_mask_secret(api_key)}")
    print(f"backend_url={backend_url}")
    print(f"timeout={timeout}s")
    print("")

    results: list[StepResult] = []
    results.append(_resolve_dns(base_url))

    auth_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    models_url = f"{base_url.rstrip('/')}/models"
    chat_url = f"{base_url.rstrip('/')}/chat/completions"

    # Upstream tests (direct)
    results.append(_request(method="GET", url=models_url, headers=auth_headers, timeout=timeout))
    results.append(_request(method="GET", url=models_url, headers=auth_headers, timeout=timeout, force_ipv4=True))
    results.append(
        _request(
            method="POST",
            url=chat_url,
            headers=auth_headers,
            timeout=timeout,
            payload={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个有用的助手"},
                    {"role": "user", "content": "你好，请介绍一下你自己"},
                ],
                "max_tokens": 64,
                "temperature": 0.2,
                "stream": False,
            },
        )
    )
    results.append(
        _request(
            method="POST",
            url=chat_url,
            headers=auth_headers,
            timeout=timeout,
            force_ipv4=True,
            payload={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个有用的助手"},
                    {"role": "user", "content": "你好，请介绍一下你自己"},
                ],
                "max_tokens": 64,
                "temperature": 0.2,
                "stream": False,
            },
        )
    )

    # Backend tests (the same path used by frontend)
    backend_health = _request(
        method="GET",
        url=f"{backend_url.rstrip('/')}/health",
        headers={},
        timeout=10,
    )
    results.append(backend_health)

    backend_test = _request(
        method="POST",
        url=f"{backend_url.rstrip('/')}/api/v1/providers/test-connection",
        headers={"Content-Type": "application/json"},
        timeout=timeout + 10,
        payload={
            "provider_type": provider_type,
            "api_key": api_key,
            "base_url": base_url,
            "model_name": model,
        },
    )
    results.append(backend_test)

    backend_fetch_models = _request(
        method="POST",
        url=f"{backend_url.rstrip('/')}/api/v1/providers/fetch-models",
        headers={"Content-Type": "application/json"},
        timeout=timeout + 10,
        payload={
            "provider_type": provider_type,
            "api_key": api_key,
            "base_url": base_url,
        },
    )
    results.append(backend_fetch_models)

    for result in results:
        _print_result(result)

    print("")
    direct_ok = any(
        item.ok and item.name.startswith("POST") and "/chat/completions" in item.name and item.status_code == 200
        for item in results
    )
    backend_ok = any(
        item.ok and item.name.endswith("/api/v1/providers/test-connection") and item.status_code == 200
        for item in results
    )

    summary = {
        "direct_chat_ok": direct_ok,
        "backend_test_ok": backend_ok,
        "timestamp": int(time.time()),
    }
    print("summary:", json.dumps(summary, ensure_ascii=False))

    if direct_ok and backend_ok:
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose OpenAI-compatible provider connectivity.")
    parser.add_argument("--api-key", default=os.getenv("SILICONFLOW_API_KEY", ""), help="Provider API key")
    parser.add_argument("--base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--model", default="Pro/zai-org/GLM-4.7")
    parser.add_argument("--provider-type", default="openai_compatible", choices=["openai", "openai_compatible"])
    parser.add_argument("--backend-url", default="http://127.0.0.1:8899")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.api_key.strip():
        print("Missing API key. Provide --api-key or set SILICONFLOW_API_KEY.")
        return 2
    return run_diagnostics(
        api_key=args.api_key.strip(),
        base_url=args.base_url.strip(),
        model=args.model.strip(),
        provider_type=args.provider_type.strip(),
        backend_url=args.backend_url.strip(),
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
