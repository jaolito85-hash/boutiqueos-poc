"""
04_painel.py — Servidor web do painel da Aline

Roda local na máquina dela (ou no VPS quando for produção).
Expõe API REST + serve o HTML do painel.

Como rodar:
    pip install flask flask-cors
    python 04_painel.py

Abre http://localhost:8000 no navegador.
"""

import sys
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).parent))
from database import (
    init_db,
    get_ready_queue,
    mark_as_sent,
    mark_as_skipped,
    mark_as_not_client,
    mark_as_replied,
    mark_as_entered_group,
    mark_as_customer,
    create_order,
    get_customer_by_username,
    get_stats,
    can_send_more_today,
    DAILY_LIMIT,
)
from catalogo import bp_catalogo
from leads_import import bp_leads
from prospects_review import bp_review
from competitor_panel import bp_competitor
from insights import bp_insights
from sales_panel import bp_sales
import json

app = Flask(__name__, static_folder="painel", static_url_path="")
CORS(app)
app.register_blueprint(bp_catalogo)
app.register_blueprint(bp_leads)
app.register_blueprint(bp_review)
app.register_blueprint(bp_competitor)
app.register_blueprint(bp_insights)
app.register_blueprint(bp_sales)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route("/api/queue")
def api_queue():
    """Fila de prospects prontos pra enviar."""
    from product_matcher import (
        match_product_for_lead,
        extract_text_for_matching,
        serialize_suggestion,
    )
    queue = get_ready_queue(limit=30)
    for p in queue:
        p["razoes"] = json.loads(p["razoes"]) if p.get("razoes") else []
        p["sinais"] = json.loads(p["sinais"]) if p.get("sinais") else []
        texto = extract_text_for_matching(p)
        p["produto_sugerido"] = serialize_suggestion(match_product_for_lead(texto))
    return jsonify(queue)


@app.route("/api/stats")
def api_stats():
    """Estatísticas pro topo do painel."""
    return jsonify(get_stats())


@app.route("/api/action/sent", methods=["POST"])
def api_action_sent():
    """Aline confirmou que enviou a DM.

    Body: { username, mensagem? (snapshot do que efetivamente saiu),
            plataforma? (default 'instagram') }
    """
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    mensagem = body.get("mensagem")  # opcional — alimenta sent_messages
    plataforma = (body.get("plataforma") or "instagram").lower().strip()
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    if not can_send_more_today():
        return jsonify({
            "ok": False,
            "error": f"Limite diário ({DAILY_LIMIT}) atingido. Volte amanhã."
        }), 429
    ok = mark_as_sent(username, mensagem_enviada=mensagem, plataforma=plataforma)
    if not ok:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/action/skip", methods=["POST"])
def api_action_skip():
    username = request.json.get("username")
    mark_as_skipped(username)
    return jsonify({"ok": True})


@app.route("/api/action/discard", methods=["POST"])
def api_action_discard():
    username = request.json.get("username")
    mark_as_not_client(username)
    return jsonify({"ok": True})


# ============================================================================
# CONVERSÃO — Aline marca o que aconteceu depois do envio
# ============================================================================

@app.route("/api/action/replied", methods=["POST"])
def api_action_replied():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    plataforma = (body.get("plataforma") or "instagram").lower().strip()
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    ok = mark_as_replied(username, plataforma=plataforma)
    if not ok:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/action/entered_group", methods=["POST"])
def api_action_entered_group():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    plataforma = (body.get("plataforma") or "instagram").lower().strip()
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    ok = mark_as_entered_group(username, plataforma=plataforma)
    if not ok:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404
    return jsonify({"ok": True})


@app.route("/api/action/comprou", methods=["POST"])
def api_action_comprou():
    """Marca prospect como cliente E registra uma order (fonte de verdade pra LTV/recompra).

    Body:
        username     (obrigatório)
        plataforma   (default 'instagram')
        valor        (opcional, mas recomendado — sem ele não vira order)
        canal        (opcional: whatsapp_dm | grupo_vip | loja_fisica | meta_ad | organico_outro)
        notas        (opcional)
    """
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    plataforma = (body.get("plataforma") or "instagram").lower().strip()
    canal = (body.get("canal") or "").strip() or None
    notas = (body.get("notas") or "").strip() or None
    valor_raw = body.get("valor")
    valor = None
    if valor_raw not in (None, ""):
        try:
            valor = float(valor_raw)
            if valor < 0:
                return jsonify({"ok": False, "error": "valor não pode ser negativo"}), 400
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "valor inválido"}), 400
    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400

    # 1. promove a cliente (status=CUSTOMER, customer_at, valor_compra)
    ok = mark_as_customer(username, plataforma=plataforma, valor=valor)
    if not ok:
        return jsonify({"ok": False, "error": "lead não encontrado"}), 404

    # 2. cria a order correspondente (suporta recompra — chamadas subsequentes
    # com mesmo username criam novas linhas em orders sem reescrever valor_compra)
    order_id = None
    if valor is not None and valor > 0:
        customer = get_customer_by_username(username, plataforma=plataforma)
        if customer:
            order_id = create_order(
                customer["id"],
                valor,
                canal=canal,
                notas=notas,
            )
    return jsonify({"ok": True, "order_id": order_id})


# ============================================================================
# SERVE PAINEL
# ============================================================================

@app.route("/")
def index():
    return send_from_directory("painel", "index.html")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    init_db()
    print("\n" + "=" * 60)
    print("PAINEL haus outbound rodando!")
    print("=" * 60)
    print("Abra no navegador: http://localhost:8000")
    print("Ctrl+C pra parar")
    print("=" * 60 + "\n")
    app.run(host="0.0.0.0", port=8000, debug=False)
