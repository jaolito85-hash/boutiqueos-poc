"""Wrapper REST mínimo da Evolution API.

Documentação: https://doc.evolution-api.com/

Endpoints usados:
  POST /message/sendText/{instance}   — texto
  POST /message/sendMedia/{instance}  — imagem/áudio/documento via URL

Variáveis de ambiente:
  EVOLUTION_API_URL          ex: https://evo.seudominio.com
  EVOLUTION_INSTANCE_NAME    nome da instância já pareada
  EVOLUTION_API_KEY          apikey global (header `apikey`)
"""

import os
import requests
from typing import Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass


class EvolutionError(Exception):
    pass


DEFAULT_WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE"]


def _base_url() -> str:
    url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
    if not url:
        raise EvolutionError("EVOLUTION_API_URL não configurada")
    return url


def _instance() -> str:
    inst = os.getenv("EVOLUTION_INSTANCE_NAME", "").strip()
    if not inst:
        raise EvolutionError("EVOLUTION_INSTANCE_NAME não configurada")
    return inst


def _headers() -> dict:
    key = os.getenv("EVOLUTION_API_KEY", "").strip()
    if not key:
        raise EvolutionError("EVOLUTION_API_KEY não configurada")
    return {"apikey": key, "Content-Type": "application/json"}


def webhook_url(explicit_url: Optional[str] = None) -> str:
    """Resolve a URL publica que a Evolution deve chamar."""
    url = (explicit_url or os.getenv("EVOLUTION_WEBHOOK_URL", "")).strip()
    if url:
        return url.rstrip("/")

    public_base = os.getenv("HAUS_PUBLIC_URL", "").strip().rstrip("/")
    if public_base:
        return f"{public_base}/webhook/wa"

    raise EvolutionError(
        "Configure EVOLUTION_WEBHOOK_URL=https://seu-dominio/webhook/wa "
        "ou HAUS_PUBLIC_URL=https://seu-dominio"
    )


def normalize_phone(phone: str) -> str:
    """Remove caracteres não-numéricos. Evolution aceita formato E.164 sem o '+'."""
    return "".join(c for c in (phone or "") if c.isdigit())


def send_text(phone: str, text: str, quoted_message_id: Optional[str] = None) -> dict:
    """Envia texto. Retorna o payload da Evolution (contém o message id)."""
    url = f"{_base_url()}/message/sendText/{_instance()}"
    body: dict = {
        "number": normalize_phone(phone),
        "text": text,
    }
    if quoted_message_id:
        body["quoted"] = {"key": {"id": quoted_message_id}}
    r = requests.post(url, json=body, headers=_headers(), timeout=20)
    if r.status_code >= 400:
        raise EvolutionError(f"send_text falhou: {r.status_code} {r.text[:300]}")
    return r.json()


def send_media(phone: str, media_url: str, caption: str = "",
               media_type: str = "image", filename: Optional[str] = None) -> dict:
    """Envia mídia por URL pública. media_type: image | video | document | audio."""
    url = f"{_base_url()}/message/sendMedia/{_instance()}"
    body: dict = {
        "number": normalize_phone(phone),
        "mediatype": media_type,
        "media": media_url,
        "caption": caption,
    }
    if filename:
        body["fileName"] = filename
    r = requests.post(url, json=body, headers=_headers(), timeout=30)
    if r.status_code >= 400:
        raise EvolutionError(f"send_media falhou: {r.status_code} {r.text[:300]}")
    return r.json()


def configure_webhook(
    url: Optional[str] = None,
    events: Optional[list[str]] = None,
    webhook_by_events: bool = False,
    webhook_base64: bool = False,
) -> dict:
    """Configura o webhook da instancia Evolution para apontar ao painel haus."""
    resolved_url = webhook_url(url)
    payload = {
        "enabled": True,
        "url": resolved_url,
        "webhookByEvents": webhook_by_events,
        "webhookBase64": webhook_base64,
        "events": events or DEFAULT_WEBHOOK_EVENTS,
    }
    r = requests.post(
        f"{_base_url()}/webhook/set/{_instance()}",
        json=payload,
        headers=_headers(),
        timeout=20,
    )
    if r.status_code == 400 and "property" in r.text and "webhook" in r.text:
        r = requests.post(
            f"{_base_url()}/webhook/set/{_instance()}",
            json={"webhook": payload},
            headers=_headers(),
            timeout=20,
        )
    if r.status_code >= 400:
        raise EvolutionError(f"configure_webhook falhou: {r.status_code} {r.text[:300]}")
    return r.json()


def find_webhook() -> dict:
    """Busca a configuracao de webhook atual da instancia Evolution."""
    r = requests.get(
        f"{_base_url()}/webhook/find/{_instance()}",
        headers=_headers(),
        timeout=10,
    )
    if r.status_code >= 400:
        raise EvolutionError(f"find_webhook falhou: {r.status_code} {r.text[:300]}")
    return r.json()


def instance_status() -> dict:
    """Verifica se a instância está conectada (pra healthcheck)."""
    url = f"{_base_url()}/instance/connectionState/{_instance()}"
    r = requests.get(url, headers=_headers(), timeout=10)
    if r.status_code >= 400:
        raise EvolutionError(f"instance_status falhou: {r.status_code} {r.text[:300]}")
    return r.json()
