"""
competitor_panel.py — Blueprint Flask para Análise de Concorrentes do painel haus.

Rotas (registrar em 04_painel.py):
    GET    /api/concorrentes                       lista (último snapshot por handle)
    POST   /api/concorrentes/analisar              body: {url_ou_handle, posts?, refresh?}
    GET    /api/concorrentes/<handle>              último snapshot completo
    GET    /api/concorrentes/<handle>/historico    todos os snapshots desse handle
    GET    /api/concorrentes/<handle>/diff?dias=30 delta entre snapshots
    DELETE /api/concorrentes/<handle>              remove todos os snapshots
"""

from __future__ import annotations

import os
import re
import sqlite3
import traceback
from flask import Blueprint, jsonify, request

from competitor_intel import analisar as ci_analisar, diff as ci_diff
from database import (
    DB_PATH,
    count_competitor_runs_today,
    get_connection,
    get_latest_competitor_snapshot,
    list_competitor_snapshots,
    log_competitor_run,
)


bp_competitor = Blueprint("competitor", __name__)

# Throttle diário — protege contra abuso/spam. Configurável por env.
DAILY_LIMIT = int(os.getenv("HAUS_COMPETITOR_DAILY_LIMIT", "10"))
# Posts coletados por análise (fixo no backend — não exposto ao cliente).
POSTS_PER_RUN = int(os.getenv("HAUS_COMPETITOR_POSTS_LIMIT", "30"))


# ============================================================================
# HELPERS
# ============================================================================

_INSTAGRAM_HANDLE_RE = re.compile(r"^[A-Za-z0-9_.]{1,30}$")


def extract_handle(raw: str) -> str | None:
    """Extrai um @handle válido de Instagram a partir de URL ou texto livre.

    Aceita:
        'mayara.home'                           -> 'mayara.home'
        '@mayara.home'                          -> 'mayara.home'
        'https://www.instagram.com/mayara.home' -> 'mayara.home'
        'https://instagram.com/mayara.home/'    -> 'mayara.home'
        'instagram.com/mayara.home/reels/x'     -> 'mayara.home'

    Retorna None se não conseguir extrair handle válido.
    """
    if not raw:
        return None
    s = raw.strip()
    # remover protocolos e prefixos comuns
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(www\.)?instagram\.com/", "", s, flags=re.IGNORECASE)
    s = s.lstrip("@").strip("/")
    # pega o primeiro segmento (antes de query, hash, ou path adicional)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.lower()
    if not s or not _INSTAGRAM_HANDLE_RE.match(s):
        return None
    return s


def _last_snapshot_per_handle(plataforma: str = "instagram") -> list[dict]:
    """Retorna apenas o snapshot mais recente de cada handle, ordenado por data desc."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT cs.* FROM competitor_snapshots cs
            JOIN (
                SELECT username, MAX(snapshot_date) AS d, MAX(id) AS max_id
                FROM competitor_snapshots
                WHERE plataforma = ?
                GROUP BY username
            ) t ON cs.username = t.username AND cs.id = t.max_id
            WHERE cs.plataforma = ?
            ORDER BY cs.snapshot_date DESC, cs.username ASC
        """, (plataforma, plataforma)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # injetar campos compactos para a lista (sem parsear payload pesado)
            out.append({
                "id": d["id"],
                "username": d["username"],
                "plataforma": d["plataforma"],
                "snapshot_date": d["snapshot_date"],
                "followers": d["followers"],
                "posts_total": d["posts_total"],
                "engagement_rate": d["engagement_rate"],
                "freq_posts_dia": d["freq_posts_dia"],
                "posicionamento": d["posicionamento"],
                "custo_usd": d["custo_usd"],
                "created_at": d["created_at"],
            })
        return out
    finally:
        conn.close()


def _full_snapshot(handle: str) -> dict | None:
    snap = get_latest_competitor_snapshot(handle)
    if not snap:
        return None
    return snap


# ============================================================================
# ROTAS
# ============================================================================

@bp_competitor.route("/api/concorrentes", methods=["GET"])
def api_list():
    """Lista todos os concorrentes (último snapshot por handle)."""
    return jsonify(_last_snapshot_per_handle())


@bp_competitor.route("/api/concorrentes/uso-hoje", methods=["GET"])
def api_uso_hoje():
    """Retorna o uso diário e o limite — usado pela UI pra mostrar contador."""
    used = count_competitor_runs_today()
    return jsonify({
        "used": used,
        "limit": DAILY_LIMIT,
        "remaining": max(0, DAILY_LIMIT - used),
        "can_run": used < DAILY_LIMIT,
    })


@bp_competitor.route("/api/concorrentes/analisar", methods=["POST"])
def api_analisar():
    """Roda análise sincrona. Posts e refresh são controlados pelo servidor (não pelo cliente),
    exceto refresh=True que só o botão 'Re-analisar' do detalhe pode disparar."""
    data = request.get_json(silent=True) or {}
    raw = data.get("url_ou_handle") or data.get("handle") or ""
    handle = extract_handle(raw)
    if not handle:
        return jsonify({
            "ok": False,
            "error": "Não consegui identificar o perfil. Verifique o link do Instagram ou o @username.",
        }), 400

    # Throttle diário — proteção principal contra abuso.
    used_today = count_competitor_runs_today()
    if used_today >= DAILY_LIMIT:
        return jsonify({
            "ok": False,
            "error": f"Você já fez {used_today} análises hoje (limite: {DAILY_LIMIT}). Novas análises liberam amanhã.",
            "code": "DAILY_LIMIT_REACHED",
        }), 429

    # Params controlados pelo servidor — cliente não escolhe (evita spam e custos).
    force_refresh = bool(data.get("refresh") or False)

    try:
        out = ci_analisar(handle, posts_limit=POSTS_PER_RUN, force_refresh=force_refresh)
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        # Mensagens amigáveis sem expor tecnologias subjacentes (Apify/OpenAI) à cliente.
        if "insufficient_quota" in low or "exceeded your current quota" in low:
            return jsonify({
                "ok": False,
                "error": "Análise temporariamente indisponível. Por favor, avise a equipe Haus pra liberar.",
                "code": "SERVICE_UNAVAILABLE",
            }), 503
        if "rate limit" in low or "429" in low:
            return jsonify({
                "ok": False,
                "error": "Muitas análises ao mesmo tempo — tente novamente em alguns segundos.",
                "code": "RATE_LIMIT",
            }), 429
        if "openai_api_key" in low or "apify_token" in low:
            # Erro de configuração — visível apenas se a equipe técnica olhar
            return jsonify({
                "ok": False,
                "error": "Análise temporariamente indisponível. Avise a equipe Haus.",
                "code": "CONFIG_ERROR",
            }), 503
        if isinstance(e, RuntimeError):
            return jsonify({
                "ok": False,
                "error": "Não consegui completar a análise deste perfil. Tente outro ou aguarde alguns minutos.",
            }), 500
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "error": "Algo inesperado aconteceu. Tente novamente em instantes.",
        }), 500

    # Sucesso — registra o run pro contador diário e responde
    log_competitor_run(handle, custo_usd=out.get("custo_usd"))
    full = _full_snapshot(handle)
    return jsonify({
        "ok": True,
        "handle": handle,
        "snapshot": full,
        "uso_hoje": count_competitor_runs_today(),
        "limite_diario": DAILY_LIMIT,
    })


@bp_competitor.route("/api/concorrentes/<handle>", methods=["GET"])
def api_get(handle):
    handle = extract_handle(handle)
    if not handle:
        return jsonify({"ok": False, "error": "Handle inválido"}), 400
    snap = _full_snapshot(handle)
    if not snap:
        return jsonify({"ok": False, "error": "Nenhum snapshot encontrado para este handle"}), 404
    return jsonify(snap)


@bp_competitor.route("/api/concorrentes/<handle>/historico", methods=["GET"])
def api_historico(handle):
    handle = extract_handle(handle)
    if not handle:
        return jsonify({"ok": False, "error": "Handle inválido"}), 400
    return jsonify(list_competitor_snapshots(username=handle))


@bp_competitor.route("/api/concorrentes/<handle>/diff", methods=["GET"])
def api_diff(handle):
    handle = extract_handle(handle)
    if not handle:
        return jsonify({"ok": False, "error": "Handle inválido"}), 400
    try:
        dias = int(request.args.get("dias", 30))
    except (TypeError, ValueError):
        dias = 30
    try:
        return jsonify(ci_diff(handle, dias=dias))
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@bp_competitor.route("/api/concorrentes/<handle>", methods=["DELETE"])
def api_delete(handle):
    handle = extract_handle(handle)
    if not handle:
        return jsonify({"ok": False, "error": "Handle inválido"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "DELETE FROM competitor_snapshots WHERE username = ?",
            (handle,),
        )
        conn.commit()
        return jsonify({"ok": True, "removidos": cur.rowcount})
    finally:
        conn.close()
