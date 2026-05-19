"""
sales_panel.py — Blueprint Flask da aba Vendas (clientes + LTV + pedidos).

Padrão seguindo competitor_panel.py / insights.py.

Rotas:
    GET    /api/vendas                        lista de clientes (cards) com LTV
    GET    /api/vendas/dashboard              cards do topo (período configurável)
    GET    /api/vendas/canais                 agregação por canal no período
    GET    /api/vendas/top-ltv?limit=20       top clientes (base p/ Meta Ads)
    GET    /api/vendas/<username>             detalhe + histórico de pedidos
    POST   /api/vendas                        registra nova venda (recompra OK)
    DELETE /api/vendas/orders/<order_id>      remove order (correção de erro)
"""

from __future__ import annotations

import sqlite3
import traceback
from flask import Blueprint, jsonify, request

from database import DB_PATH
from sales import (
    dashboard_vendas,
    historico_cliente,
    listar_clientes,
    registrar_venda,
    top_ltv,
    vendas_por_canal,
)


bp_sales = Blueprint("sales", __name__)


def _parse_periodo(default: int = 30) -> int:
    raw = request.args.get("periodo_dias") or request.args.get("dias") or default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, 365))


@bp_sales.route("/api/vendas", methods=["GET"])
def api_list():
    busca = (request.args.get("busca") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 200))
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = 200
    return jsonify({"ok": True, "items": listar_clientes(busca=busca, limit=limit)})


@bp_sales.route("/api/vendas/dashboard", methods=["GET"])
def api_dashboard():
    return jsonify({"ok": True, **dashboard_vendas(_parse_periodo(30))})


@bp_sales.route("/api/vendas/canais", methods=["GET"])
def api_canais():
    return jsonify({
        "ok": True,
        "periodo_dias": _parse_periodo(30),
        "items": vendas_por_canal(_parse_periodo(30)),
    })


@bp_sales.route("/api/vendas/top-ltv", methods=["GET"])
def api_top_ltv():
    try:
        limit = int(request.args.get("limit", 20))
        limit = max(1, min(limit, 100))
    except ValueError:
        limit = 20
    return jsonify({"ok": True, "items": top_ltv(limit=limit)})


@bp_sales.route("/api/vendas/<username>", methods=["GET"])
def api_detail(username):
    plataforma = (request.args.get("plataforma") or "instagram").lower().strip()
    detalhe = historico_cliente(username, plataforma=plataforma)
    if not detalhe:
        return jsonify({"ok": False, "error": "cliente não encontrado"}), 404
    return jsonify({"ok": True, "customer": detalhe})


@bp_sales.route("/api/vendas", methods=["POST"])
def api_create():
    """Registra venda. Aceita recompra (chamadas subsequentes pro mesmo username)."""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    plataforma = (body.get("plataforma") or "instagram").lower().strip()
    canal = (body.get("canal") or "").strip() or None
    utm_source = (body.get("utm_source") or "").strip() or None
    utm_campaign = (body.get("utm_campaign") or "").strip() or None
    notas = (body.get("notas") or "").strip() or None
    produtos = body.get("produtos") or None
    valor_raw = body.get("valor") or body.get("valor_brl")

    if not username:
        return jsonify({"ok": False, "error": "username obrigatório"}), 400
    try:
        valor = float(valor_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "valor obrigatório e numérico"}), 400
    if valor <= 0:
        return jsonify({"ok": False, "error": "valor precisa ser > 0"}), 400

    try:
        out = registrar_venda(
            username,
            valor,
            plataforma=plataforma,
            canal=canal,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
            produtos=produtos if isinstance(produtos, list) else None,
            notas=notas,
        )
    except LookupError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"erro inesperado: {e}"}), 500
    return jsonify(out)


@bp_sales.route("/api/vendas/orders/<int:order_id>", methods=["DELETE"])
def api_delete_order(order_id):
    """Remove uma order (útil pra corrigir digitação errada). Não desfaz
    status='CUSTOMER' nem valor_compra — só apaga a linha de orders."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({"ok": False, "error": "order não encontrada"}), 404
        return jsonify({"ok": True, "removidos": cur.rowcount})
    finally:
        conn.close()
