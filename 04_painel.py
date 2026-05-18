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
    get_stats,
    can_send_more_today,
    DAILY_LIMIT,
)
from catalogo import bp_catalogo
import json

app = Flask(__name__, static_folder="painel", static_url_path="")
CORS(app)
app.register_blueprint(bp_catalogo)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route("/api/queue")
def api_queue():
    """Fila de prospects prontos pra enviar."""
    queue = get_ready_queue(limit=30)
    # Parsear razoes/sinais como JSON
    for p in queue:
        p["razoes"] = json.loads(p["razoes"]) if p.get("razoes") else []
        p["sinais"] = json.loads(p["sinais"]) if p.get("sinais") else []
    return jsonify(queue)


@app.route("/api/stats")
def api_stats():
    """Estatísticas pro topo do painel."""
    return jsonify(get_stats())


@app.route("/api/action/sent", methods=["POST"])
def api_action_sent():
    """Aline confirmou que enviou a DM."""
    username = request.json.get("username")
    if not can_send_more_today():
        return jsonify({
            "ok": False,
            "error": f"Limite diário ({DAILY_LIMIT}) atingido. Volte amanhã."
        }), 429
    mark_as_sent(username)
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
