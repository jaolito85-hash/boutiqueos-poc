"""API REST do agente WhatsApp — endpoints consumidos pelo painel.

Endpoints:
  GET  /api/wa/contacts                  — lista conversas
  GET  /api/wa/contacts/<id>             — detalhe de 1 contato
  GET  /api/wa/messages/<contact_id>     — histórico
  POST /api/wa/messages/<contact_id>/read — zera unread
  POST /api/wa/send                       — Aline envia mensagem manual
  POST /api/wa/handoff/<contact_id>      — assumir/devolver à IA
  GET  /api/wa/status                     — healthcheck Evolution
"""

from flask import Blueprint, request, jsonify

from database import (
    get_wa_contacts,
    get_wa_contact,
    get_wa_contact_by_phone,
    get_wa_messages,
    mark_wa_read,
    set_wa_mode,
    record_wa_message,
    upsert_wa_contact,
)
from wa import evolution_client


bp_wa_api = Blueprint("wa_api", __name__)


@bp_wa_api.route("/api/wa/contacts", methods=["GET"])
def api_list_contacts():
    status = request.args.get("status", "ativo")
    limit = int(request.args.get("limit", 200))
    contacts = get_wa_contacts(status=status, limit=limit)
    return jsonify({"ok": True, "contacts": contacts})


@bp_wa_api.route("/api/wa/contacts/<int:contact_id>", methods=["GET"])
def api_get_contact(contact_id: int):
    c = get_wa_contact(contact_id)
    if not c:
        return jsonify({"ok": False, "error": "contato não encontrado"}), 404
    return jsonify({"ok": True, "contact": c})


@bp_wa_api.route("/api/wa/messages/<int:contact_id>", methods=["GET"])
def api_list_messages(contact_id: int):
    limit = int(request.args.get("limit", 50))
    before_id = request.args.get("before_id")
    before_id = int(before_id) if before_id else None
    msgs = get_wa_messages(contact_id, limit=limit, before_id=before_id)
    return jsonify({"ok": True, "messages": msgs})


@bp_wa_api.route("/api/wa/messages/<int:contact_id>/read", methods=["POST"])
def api_mark_read(contact_id: int):
    mark_wa_read(contact_id)
    return jsonify({"ok": True})


@bp_wa_api.route("/api/wa/send", methods=["POST"])
def api_send():
    """Envia mensagem (Aline manual ou agente).

    Body:
      { contact_id?: int, phone?: str, content?: str, media_url?: str, media_type?: str }

    Precisa de pelo menos um identificador (contact_id ou phone) e (content OU media_url).
    """
    body = request.get_json(silent=True) or {}
    contact_id = body.get("contact_id")
    phone = (body.get("phone") or "").strip()
    content = body.get("content")
    media_url = body.get("media_url")
    media_type = body.get("media_type") or "image"

    if not contact_id and not phone:
        return jsonify({"ok": False, "error": "contact_id ou phone obrigatório"}), 400
    if not content and not media_url:
        return jsonify({"ok": False, "error": "content ou media_url obrigatório"}), 400

    # Resolve contato
    contact = None
    if contact_id:
        contact = get_wa_contact(int(contact_id))
    elif phone:
        contact = get_wa_contact_by_phone(evolution_client.normalize_phone(phone))
        if not contact:
            # primeira interação saindo da haus pra um número novo
            cid = upsert_wa_contact(evolution_client.normalize_phone(phone))
            contact = get_wa_contact(cid)
    if not contact:
        return jsonify({"ok": False, "error": "contato não encontrado"}), 404

    try:
        if media_url:
            resp = evolution_client.send_media(
                phone=contact["phone"],
                media_url=media_url,
                caption=content or "",
                media_type=media_type,
            )
        else:
            resp = evolution_client.send_text(phone=contact["phone"], text=content)
    except evolution_client.EvolutionError as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    # Tenta extrair id da resposta (formato varia)
    provider_id = None
    try:
        provider_id = (resp.get("key") or {}).get("id") or resp.get("id")
    except Exception:
        pass

    record_wa_message(
        contact_id=contact["id"],
        role="assistant",
        direction="out",
        content=content,
        media_url=media_url,
        media_type=media_type if media_url else None,
        provider_message_id=provider_id,
        status="sent",
    )
    return jsonify({"ok": True, "provider_response": resp})


@bp_wa_api.route("/api/wa/handoff/<int:contact_id>", methods=["POST"])
def api_handoff(contact_id: int):
    """Assumir (mode=human) ou devolver à IA (mode=ai)."""
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    reason = body.get("reason")
    if action == "assume":
        set_wa_mode(contact_id, "human", reason=reason, requested_by="human")
        return jsonify({"ok": True, "mode": "human"})
    if action == "release":
        set_wa_mode(contact_id, "ai", reason=reason, requested_by="human")
        return jsonify({"ok": True, "mode": "ai"})
    return jsonify({"ok": False, "error": "action deve ser 'assume' ou 'release'"}), 400


@bp_wa_api.route("/api/wa/status", methods=["GET"])
def api_status():
    """Verifica conexão da instância Evolution."""
    try:
        s = evolution_client.instance_status()
        return jsonify({"ok": True, "evolution": s})
    except evolution_client.EvolutionError as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@bp_wa_api.route("/api/wa/webhook", methods=["GET"])
def api_webhook_status():
    """Mostra a configuracao de webhook gravada na Evolution."""
    try:
        configured = evolution_client.find_webhook()
        expected_url = evolution_client.webhook_url()
        return jsonify({"ok": True, "expected_url": expected_url, "webhook": configured})
    except evolution_client.EvolutionError as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@bp_wa_api.route("/api/wa/webhook/configure", methods=["POST"])
def api_configure_webhook():
    """Aponta a instancia Evolution para /webhook/wa do painel haus."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip() or None
    events = body.get("events") or None
    try:
        configured = evolution_client.configure_webhook(url=url, events=events)
        return jsonify({
            "ok": True,
            "configured_url": evolution_client.webhook_url(url),
            "webhook": configured,
        })
    except evolution_client.EvolutionError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
