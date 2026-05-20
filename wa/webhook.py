"""Webhook receiver da Evolution API.

A Evolution manda eventos via POST pro URL configurado no painel dela.
Eventos que nos interessam:
  - MESSAGES_UPSERT  (msg recebida ou enviada — chega pra ambos os sentidos)
  - MESSAGES_UPDATE  (ack: delivered, read)

Parser tolerante: formato varia entre versões da Evolution. Salvamos sempre o
payload bruto em `wa_messages.raw_json` pra debug.
"""

import json
from flask import Blueprint, request, jsonify

from database import upsert_wa_contact, record_wa_message


bp_wa_webhook = Blueprint("wa_webhook", __name__)

EVENT_SLUGS = {
    "messages-upsert": "MESSAGES_UPSERT",
    "messages-update": "MESSAGES_UPDATE",
    "connection-update": "CONNECTION_UPDATE",
}


def _extract_phone_from_jid(jid: str) -> str:
    """JID Evolution vem como '5544912345678@s.whatsapp.net' — pega só o número."""
    if not jid:
        return ""
    return jid.split("@")[0]


def _parse_message_event(data: dict) -> dict | None:
    """Extrai campos relevantes de um evento MESSAGES_UPSERT.

    Retorna dict pronto pra record_wa_message, ou None se não for msg útil
    (status update, msg do sistema, etc).
    """
    key = data.get("key") or {}
    remote_jid = key.get("remoteJid") or ""
    # Ignora msgs em grupos (terminam com @g.us)
    if "@g.us" in remote_jid:
        return None

    phone = _extract_phone_from_jid(remote_jid)
    if not phone:
        return None

    # fromMe=True significa que a mensagem foi enviada PELA instância (saída).
    from_me = bool(key.get("fromMe", False))
    direction = "out" if from_me else "in"

    provider_message_id = key.get("id")
    nome = data.get("pushName") or None

    msg = data.get("message") or {}
    content = None
    media_url = None
    media_type = None

    if "conversation" in msg:
        content = msg["conversation"]
    elif "extendedTextMessage" in msg:
        content = (msg["extendedTextMessage"] or {}).get("text")
    elif "imageMessage" in msg:
        media_type = "image"
        content = (msg["imageMessage"] or {}).get("caption") or ""
        media_url = (msg["imageMessage"] or {}).get("url")
    elif "videoMessage" in msg:
        media_type = "video"
        content = (msg["videoMessage"] or {}).get("caption") or ""
        media_url = (msg["videoMessage"] or {}).get("url")
    elif "audioMessage" in msg:
        media_type = "audio"
        content = "[áudio recebido]"
        media_url = (msg["audioMessage"] or {}).get("url")
    elif "documentMessage" in msg:
        media_type = "document"
        content = (msg["documentMessage"] or {}).get("fileName") or "[documento]"
        media_url = (msg["documentMessage"] or {}).get("url")
    elif "stickerMessage" in msg:
        media_type = "sticker"
        content = "[sticker]"
    else:
        # Tipo não suportado — registramos como nota
        content = f"[mensagem não suportada: {list(msg.keys())[:3]}]"

    return {
        "phone": phone,
        "nome": nome,
        "direction": direction,
        "provider_message_id": provider_message_id,
        "content": content,
        "media_url": media_url,
        "media_type": media_type,
    }


@bp_wa_webhook.route("/webhook/wa", methods=["POST"])
@bp_wa_webhook.route("/webhook/wa/<path:event_slug>", methods=["POST"])
def webhook_wa(event_slug: str | None = None):
    """Endpoint que a Evolution chama. Retorna 200 rápido (não-bloqueante)."""
    payload = request.get_json(silent=True) or {}
    event = payload.get("event") or payload.get("type") or ""
    if not event and event_slug:
        event = EVENT_SLUGS.get(event_slug.strip("/").lower(), event_slug)

    # Evolution às vezes manda { event, data: {...} } e às vezes a data no root.
    data = payload.get("data") or payload

    if event in ("messages.upsert", "MESSAGES_UPSERT", "message"):
        # data pode ser { messages: [...] } ou direto a msg
        msgs = data.get("messages") if isinstance(data.get("messages"), list) else [data]
        for m in msgs:
            parsed = _parse_message_event(m)
            if not parsed:
                continue
            contact_id = upsert_wa_contact(parsed["phone"], parsed["nome"])
            record_wa_message(
                contact_id=contact_id,
                role="user" if parsed["direction"] == "in" else "assistant",
                direction=parsed["direction"],
                content=parsed["content"],
                media_url=parsed["media_url"],
                media_type=parsed["media_type"],
                provider_message_id=parsed["provider_message_id"],
                status="received" if parsed["direction"] == "in" else "sent",
                raw_json=json.dumps(m)[:8000],
            )
        return jsonify({"ok": True}), 200

    if event in ("messages.update", "MESSAGES_UPDATE"):
        # ack de delivered/read — fica pra v2 atualizar status
        return jsonify({"ok": True, "ignored": "update"}), 200

    # Evento desconhecido: ignora silencioso (Evolution manda muito ruído)
    if event in ("connection.update", "CONNECTION_UPDATE"):
        return jsonify({"ok": True, "ignored": "connection"}), 200

    return jsonify({"ok": True, "ignored": event}), 200


@bp_wa_webhook.route("/webhook/wa", methods=["GET"])
def webhook_wa_verify():
    """Healthcheck/verify endpoint."""
    return jsonify({"ok": True, "service": "haus-wa-webhook"}), 200
