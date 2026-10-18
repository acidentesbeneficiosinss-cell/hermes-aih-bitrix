"""Cliente REST mínimo do Bitrix24 por webhook de entrada.

O token do webhook é segredo: nunca aparece em mensagens de erro, logs ou respostas (ver `redact`).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests

_WEBHOOK_RE = re.compile(r"^https://([^/\s]+)/rest/(\d+)/([A-Za-z0-9]+)/?$")
_RETRYABLE_CODES = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR"}


class BitrixError(Exception):
    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code
        self.description = description

    @property
    def retryable(self) -> bool:
        """Só falhas transitórias (rede, 5xx, limite de taxa) podem se resolver sozinhas."""
        return self.code in _RETRYABLE_CODES or self.code == "NETWORK" or self.code.startswith("HTTP_5")


def redact(text: str, webhook_url: str) -> str:
    """Troca o token do webhook (e qualquer `/rest/<id>/<token>/`) por <TOKEN>."""
    match = _WEBHOOK_RE.match((webhook_url or "").strip())
    if match:
        text = text.replace(match.group(3), "<TOKEN>")
    return re.sub(r"(/rest/\d+/)[A-Za-z0-9]+(/)", r"\1<TOKEN>\2", text)


class BitrixClient:
    def __init__(
        self,
        webhook_url: str,
        *,
        session: Any = None,
        timeout: float = 60.0,
        min_interval: float = 0.5,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        match = _WEBHOOK_RE.match((webhook_url or "").strip())
        if not match:  # não repete a URL na mensagem: ela pode conter o token
            raise ValueError("BITRIX_WEBHOOK_URL inválida: esperado https://<portal>/rest/<id>/<token>/")
        self.host = match.group(1).lower()
        self._base = f"https://{match.group(1)}/rest/{match.group(2)}/{match.group(3)}/"
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call: float | None = None

    # ------------------------------------------------------------------ infra
    def _redact(self, text: str) -> str:
        return redact(text, self._base)

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self._min_interval - (self._monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._monotonic()

    # ------------------------------------------------------------------ API
    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base}{method}.json"
        last_error = BitrixError("UNKNOWN", "falha sem detalhes")
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                response = self._session.post(url, json=params or {}, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = BitrixError("NETWORK", f"falha de rede ({type(exc).__name__})")
                self._backoff(attempt)
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if payload is None:
                last_error = BitrixError(f"HTTP_{response.status_code}", "resposta sem JSON do Bitrix")
                if response.status_code >= 500:
                    self._backoff(attempt)
                    continue
                raise last_error
            if "error" in payload:
                code = str(payload["error"])
                last_error = BitrixError(code, self._redact(str(payload.get("error_description", ""))))
                if code in _RETRYABLE_CODES or response.status_code >= 500:
                    self._backoff(attempt)
                    continue
                raise last_error
            if response.status_code >= 500:
                last_error = BitrixError(f"HTTP_{response.status_code}", "erro do servidor Bitrix")
                self._backoff(attempt)
                continue
            return payload
        raise last_error

    def _backoff(self, attempt: int) -> None:
        if attempt < self._max_retries:
            self._sleep(min(2**attempt, 8))

    def result(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.call(method, params).get("result")

    def list_all(self, method: str, params: dict[str, Any], key: str | None = None, limit: int = 2000) -> list[Any]:
        items: list[Any] = []
        start = 0
        while len(items) < limit:
            payload = self.call(method, {**params, "start": start})
            result = payload.get("result")
            chunk = result.get(key) if key and isinstance(result, dict) else result
            items.extend(chunk or [])
            if payload.get("next") is None:
                break
            start = payload["next"]
        return items[:limit]

    def download(self, url: str, max_bytes: int) -> bytes:
        """Baixa um anexo. Só aceita https no host do portal e não segue redirecionamentos (anti-SSRF)."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != self.host:
            raise BitrixError("DOWNLOAD_HOST", "URL de download fora do portal Bitrix configurado")
        response = self._session.get(url, stream=True, timeout=self._timeout, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise BitrixError(f"HTTP_{response.status_code}", "falha ao baixar o anexo")
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise BitrixError("TOO_LARGE", f"anexo maior que o limite de {max_bytes} bytes")
            buffer = bytearray()
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                buffer += chunk
                if len(buffer) > max_bytes:
                    raise BitrixError("TOO_LARGE", f"anexo maior que o limite de {max_bytes} bytes")
            return bytes(buffer)
        finally:
            response.close()
