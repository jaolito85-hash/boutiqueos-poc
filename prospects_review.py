"""
prospects_review.py — Blueprint Flask para a tela "Revisar leads"

Rotas:
    GET    /api/review/queue                 — lista paginada com filtros
    GET    /api/review/prospect/<id>         — detalhe completo de um lead
    POST   /api/review/prospect/<id>/approve — REVIEW -> READY
    POST   /api/review/prospect/<id>/discard — REVIEW -> DISCARDED
    PATCH  /api/review/prospect/<id>/message — atualiza apenas a DM
    POST   /api/review/bulk/approve          — body: {ids:[...]} bulk REVIEW->READY
    POST   /api/review/bulk/discard          — body: {ids:[...]} bulk REVIEW->DISCARDED
    POST   /api/review/bulk/restore          — body: {ids:[...]} (undo) ->REVIEW

Filtros suportados em /queue (query string):
    plataforma   instagram | tiktok
    temperatura  quente | morno | frio
    intent       perguntando_preco | buscando_atendimento | perguntando_local
    busca        substring (LIKE) em username/source_loja
    sort         score_desc (default) | score_asc | recent | oldest
    page         1-indexed
    page_size    máx 100 (default 30)
"""

import json
from flask import Blueprint, request, jsonify

from database import (
    list_review_prospects,
    get_prospect_by_id,
    set_prospect_status,
    bulk_set_status,
    update_prospect_message,
)

bp_review = Blueprint("review", __name__)

MAX_BULK = 100


def _serialize(p: dict) -> dict:
    """Parseia campos JSON e devolve um shape estável pro front."""
    if not p:
        return p
    out = dict(p)
    out["razoes"] = json.loads(out["razoes"]) if out.get("razoes") else []
    out["sinais"] = json.loads(out["sinais"]) if out.get("sinais") else []
    try:
        out["raw_data"] = json.loads(out["raw_data"]) if out.get("raw_data") else {}
    except (ValueError, TypeError):
        out["raw_data"] = {}
    # derivados convenientes pra UI
    rd = out.get("raw_data") or {}
    out["url_post"] = rd.get("url_post")
    out["url_perfil"] = rd.get("url_perfil") or _perfil_url(out["plataforma"], out["username"])
    out["intent"] = rd.get("intent")
    out["temperatura"] = rd.get("temperatura")
    out["urgencia"] = rd.get("urgencia")
    out["evidencia"] = rd.get("evidencia") or (out["razoes"][0] if out["razoes"] else None)
    out["texto_original"] = rd.get("texto_original")
    # Fase 2 #1 — sugestão de produto pra anexar na DM (matching de keywords).
    # Import lazy pra não pagar load do cache em rotas que não usam.
    from product_matcher import (
        match_product_for_lead,
        extract_text_for_matching,
        serialize_suggestion,
    )
    out["produto_sugerido"] = serialize_suggestion(
        match_product_for_lead(extract_text_for_matching(out))
    )
    return out


def _perfil_url(plataforma: str, username: str) -> str | None:
    if not username:
        return None
    if plataforma == "tiktok":
        return f"https://www.tiktok.com/@{username}"
    if plataforma == "instagram":
        return f"https://www.instagram.com/{username}/"
    return None


# ----------------------------------------------------------------------------
# LISTAGEM
# ----------------------------------------------------------------------------

@bp_review.route("/api/review/queue", methods=["GET"])
def queue():
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 30))
    except ValueError:
        return jsonify({"ok": False, "error": "page e page_size devem ser inteiros"}), 400

    result = list_review_prospects(
        plataforma=request.args.get("plataforma") or None,
        temperatura=request.args.get("temperatura") or None,
        intent=request.args.get("intent") or None,
        busca=request.args.get("busca") or None,
        sort=request.args.get("sort", "score_desc"),
        page=page,
        page_size=page_size,
    )
    result["items"] = [_serialize(p) for p in result["items"]]
    return jsonify({"ok": True, **result})


# ----------------------------------------------------------------------------
# DETALHE
# ----------------------------------------------------------------------------

@bp_review.route("/api/review/prospect/<int:pid>", methods=["GET"])
def detalhe(pid):
    p = get_prospect_by_id(pid)
    if not p:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True, "prospect": _serialize(p)})


# ----------------------------------------------------------------------------
# AÇÕES INDIVIDUAIS
# ----------------------------------------------------------------------------

@bp_review.route("/api/review/prospect/<int:pid>/approve", methods=["POST"])
def approve(pid):
    p = set_prospect_status(pid, "READY", event_type="APPROVED_FROM_REVIEW")
    if not p:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True, "prospect": _serialize(p)})


@bp_review.route("/api/review/prospect/<int:pid>/discard", methods=["POST"])
def discard(pid):
    p = set_prospect_status(pid, "DISCARDED", event_type="DISCARDED_FROM_REVIEW")
    if not p:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True, "prospect": _serialize(p)})


@bp_review.route("/api/review/prospect/<int:pid>/message", methods=["PATCH"])
def edit_message(pid):
    body = request.get_json(silent=True) or {}
    mensagem = (body.get("mensagem") or "").strip()
    if not mensagem:
        return jsonify({"ok": False, "error": "mensagem vazia"}), 400
    if len(mensagem) > 2000:
        return jsonify({"ok": False, "error": "mensagem excede 2000 caracteres"}), 400
    if not update_prospect_message(pid, mensagem):
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True, "prospect": _serialize(get_prospect_by_id(pid))})


# ----------------------------------------------------------------------------
# AÇÕES EM LOTE
# ----------------------------------------------------------------------------

def _read_ids(body: dict) -> list[int] | None:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return None
    try:
        ids_int = [int(i) for i in ids]
    except (ValueError, TypeError):
        return None
    if len(ids_int) > MAX_BULK:
        return None
    return ids_int


@bp_review.route("/api/review/bulk/approve", methods=["POST"])
def bulk_approve():
    body = request.get_json(silent=True) or {}
    ids = _read_ids(body)
    if ids is None:
        return jsonify({"ok": False, "error": f"ids inválidos ou excedem {MAX_BULK}"}), 400
    n = bulk_set_status(ids, "READY", event_type="APPROVED_FROM_REVIEW")
    return jsonify({"ok": True, "affected": n, "ids": ids})


@bp_review.route("/api/review/bulk/discard", methods=["POST"])
def bulk_discard():
    body = request.get_json(silent=True) or {}
    ids = _read_ids(body)
    if ids is None:
        return jsonify({"ok": False, "error": f"ids inválidos ou excedem {MAX_BULK}"}), 400
    n = bulk_set_status(ids, "DISCARDED", event_type="DISCARDED_FROM_REVIEW")
    return jsonify({"ok": True, "affected": n, "ids": ids})


@bp_review.route("/api/review/bulk/restore", methods=["POST"])
def bulk_restore():
    """Undo: devolve leads pra REVIEW (usado pelo botão 'desfazer' do toast)."""
    body = request.get_json(silent=True) or {}
    ids = _read_ids(body)
    if ids is None:
        return jsonify({"ok": False, "error": f"ids inválidos ou excedem {MAX_BULK}"}), 400
    n = bulk_set_status(ids, "REVIEW", event_type="RESTORED_TO_REVIEW")
    return jsonify({"ok": True, "affected": n, "ids": ids})
