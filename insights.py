"""
insights.py — Blueprint Flask para o dashboard de Insights + Follow-up.

Rotas:
    GET /api/followup/queue                   listagem paginada com filtros
    GET /api/insights/funil                   contagem por status (funil)
    GET /api/insights/conversion-rates        taxas globais + tempo médio de resposta
    GET /api/insights/by-source?limit=10      agregação por source_loja
    GET /api/insights/by-intent               agregação por intent (extraído de sinais)
    GET /api/insights/by-platform             agregação por plataforma
    GET /api/insights/activity?days=30        série temporal diária

Todas as rotas são GET, read-only, sem efeito colateral. Não consomem cota
externa (Apify/OpenAI) — agregações SQL puras.
"""

import json
from flask import Blueprint, request, jsonify

from database import (
    list_followup_prospects,
    funnel_counts,
    conversion_rates,
    conversion_by_source,
    conversion_by_intent,
    conversion_by_platform,
    activity_timeseries,
)


bp_insights = Blueprint("insights", __name__)


def _perfil_url(plataforma: str, username: str) -> str | None:
    if not username:
        return None
    p = (plataforma or "").lower()
    if p == "tiktok":
        return f"https://www.tiktok.com/@{username}"
    if p == "instagram":
        return f"https://www.instagram.com/{username}/"
    return None


def _serialize_followup(p: dict) -> dict:
    """Serializa um prospect pra UI da aba Acompanhar, expandindo raw_data."""
    if not p:
        return p
    out = dict(p)
    out["razoes"] = json.loads(out["razoes"]) if out.get("razoes") else []
    out["sinais"] = json.loads(out["sinais"]) if out.get("sinais") else []
    try:
        rd = json.loads(out["raw_data"]) if out.get("raw_data") else {}
    except (ValueError, TypeError):
        rd = {}
    out["raw_data"] = rd
    out["url_perfil"] = rd.get("url_perfil") or _perfil_url(out.get("plataforma"), out.get("username"))
    out["url_post"] = rd.get("url_post")
    out["intent"] = rd.get("intent")
    out["temperatura"] = rd.get("temperatura")
    out["evidencia"] = rd.get("evidencia") or (out["razoes"][0] if out["razoes"] else None)
    return out


# ============================================================================
# FOLLOW-UP — aba "Acompanhar"
# ============================================================================

@bp_insights.route("/api/followup/queue", methods=["GET"])
def queue():
    """Lista paginada de leads em status pós-envio com filtros."""
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 30))
    except ValueError:
        return jsonify({"ok": False, "error": "page e page_size devem ser inteiros"}), 400

    statuses_param = request.args.get("statuses") or ""
    statuses = [s.strip().upper() for s in statuses_param.split(",") if s.strip()]

    days_param = request.args.get("days")
    days = None
    if days_param:
        try:
            days = int(days_param)
            if days <= 0:
                days = None
        except ValueError:
            return jsonify({"ok": False, "error": "days inválido"}), 400

    result = list_followup_prospects(
        statuses=statuses or None,
        plataforma=request.args.get("plataforma") or None,
        source_loja=request.args.get("source_loja") or None,
        days=days,
        busca=request.args.get("busca") or None,
        sort=request.args.get("sort", "sent_recent"),
        page=page,
        page_size=page_size,
    )
    result["items"] = [_serialize_followup(p) for p in result["items"]]
    return jsonify({"ok": True, **result})


# ============================================================================
# INSIGHTS — dashboard
# ============================================================================

@bp_insights.route("/api/insights/funil", methods=["GET"])
def insights_funil():
    return jsonify({"ok": True, "funil": funnel_counts()})


@bp_insights.route("/api/insights/conversion-rates", methods=["GET"])
def insights_rates():
    return jsonify({"ok": True, **conversion_rates()})


@bp_insights.route("/api/insights/by-source", methods=["GET"])
def insights_by_source():
    try:
        limit = int(request.args.get("limit", 10))
        if limit < 1 or limit > 100:
            limit = 10
    except ValueError:
        limit = 10
    return jsonify({"ok": True, "items": conversion_by_source(limit=limit)})


@bp_insights.route("/api/insights/by-intent", methods=["GET"])
def insights_by_intent():
    return jsonify({"ok": True, "items": conversion_by_intent()})


@bp_insights.route("/api/insights/by-platform", methods=["GET"])
def insights_by_platform():
    return jsonify({"ok": True, "items": conversion_by_platform()})


@bp_insights.route("/api/insights/activity", methods=["GET"])
def insights_activity():
    try:
        days = int(request.args.get("days", 30))
        if days < 1 or days > 365:
            days = 30
    except ValueError:
        days = 30
    return jsonify({"ok": True, "days": days, "items": activity_timeseries(days=days)})
