"""
sales.py — Lógica de alto nível de vendas e clientes.

Em cima dos helpers de banco em database.py, expõe funções "de produto"
pensadas pra serem consumidas pelo blueprint sales_panel.py e por scripts
futuros (08_meta_sync.py vai puxar `top_clientes_por_ltv` daqui).

Em vez de quebrar `customers` numa tabela separada, mantemos a abordagem
minimalista: cliente = prospect com status='CUSTOMER' OU pelo menos 1 order.
LTV/ticket/etc são derivados de orders por query (nunca duplicar).

Funções públicas:
    registrar_venda(username, valor_brl, canal=None, plataforma='instagram', ...)
        Orquestra: promove prospect a cliente (se ainda não é) + cria order.

    historico_cliente(username, plataforma='instagram')
        Detalhe completo: prospect + summary + orders.

    listar_clientes(busca=None, limit=200)
        Lista de cards pra UI.

    dashboard_vendas(periodo_dias=30)
        Cards de topo: clientes ativos, vendas mês, ticket médio, recompra %.

    top_ltv(limit=20)
        Ranking pra audiência Meta Ads.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from database import (
    create_order,
    get_customer_by_username,
    get_connection,
    list_clientes,
    list_orders_by_prospect,
    mark_as_customer,
    top_clientes_por_ltv,
)


def registrar_venda(
    username: str,
    valor_brl: float,
    *,
    plataforma: str = "instagram",
    canal: str | None = None,
    utm_source: str | None = None,
    utm_campaign: str | None = None,
    produtos: list[dict] | None = None,
    notas: str | None = None,
) -> dict:
    """Promove o prospect a cliente (se ainda não é) e cria order.

    Retorna o detalhe do cliente atualizado (mesmo formato de `historico_cliente`).
    """
    if not username:
        raise ValueError("username obrigatório")
    if valor_brl is None or float(valor_brl) <= 0:
        raise ValueError("valor_brl precisa ser > 0")

    # Promove a cliente. mark_as_customer atualiza valor_compra só na 1ª compra
    # (usa COALESCE — recompras não sobrescrevem valor da primeira compra).
    promovido = mark_as_customer(username, plataforma=plataforma, valor=valor_brl)
    if not promovido:
        raise LookupError(f"lead @{username} ({plataforma}) não encontrado")

    customer = get_customer_by_username(username, plataforma=plataforma)
    if not customer:
        raise LookupError(f"lead @{username} desapareceu após promoção (race?)")

    order_id = create_order(
        customer["id"],
        valor_brl,
        canal=canal,
        utm_source=utm_source,
        utm_campaign=utm_campaign,
        produtos=produtos,
        notas=notas,
    )

    # Re-busca pra retornar com order recém-criada incluída
    customer = get_customer_by_username(username, plataforma=plataforma)
    return {"ok": True, "order_id": order_id, "customer": customer}


def historico_cliente(username: str, plataforma: str = "instagram") -> dict | None:
    """Detalhe completo do cliente (prospect + summary + orders)."""
    return get_customer_by_username(username, plataforma=plataforma)


def listar_clientes(busca: str | None = None, limit: int = 200) -> list[dict]:
    return list_clientes(limit=limit, busca=busca)


def dashboard_vendas(periodo_dias: int = 30) -> dict:
    """Cards de topo da aba Vendas:
        - clientes_ativos: total de prospects status='CUSTOMER' OU com >=1 order
        - vendas_periodo_brl: SUM(orders.valor_brl) no período
        - ticket_medio_brl: AVG(orders.valor_brl) no período
        - novos_clientes_periodo: prospects que viraram cliente no período
        - recompra_pct: clientes com >=2 orders / clientes com >=1 order
    """
    desde = (datetime.now() - timedelta(days=periodo_dias)).date().isoformat()

    conn = get_connection()
    try:
        # Clientes ativos (cliente = status CUSTOMER ou pelo menos 1 order)
        row = conn.execute("""
            SELECT COUNT(DISTINCT p.id) AS n
            FROM prospects p
            LEFT JOIN orders o ON o.prospect_id = p.id
            WHERE p.status = 'CUSTOMER' OR o.id IS NOT NULL
        """).fetchone()
        clientes_ativos = int(row["n"] or 0)

        # Vendas no período
        row = conn.execute("""
            SELECT COALESCE(SUM(valor_brl), 0) AS soma,
                   COALESCE(AVG(valor_brl), 0) AS media,
                   COUNT(*) AS n_pedidos
            FROM orders
            WHERE DATE(created_at) >= ?
        """, (desde,)).fetchone()
        vendas_periodo_brl = round(row["soma"] or 0, 2)
        ticket_medio_brl = round(row["media"] or 0, 2)
        pedidos_periodo = int(row["n_pedidos"] or 0)

        # Novos clientes no período (prospects que entraram em CUSTOMER nessa janela)
        row = conn.execute("""
            SELECT COUNT(*) AS n
            FROM prospects
            WHERE customer_at IS NOT NULL AND DATE(customer_at) >= ?
        """, (desde,)).fetchone()
        novos_clientes_periodo = int(row["n"] or 0)

        # Recompra % (lifetime, não restrito ao período)
        row = conn.execute("""
            SELECT
                SUM(CASE WHEN cnt >= 2 THEN 1 ELSE 0 END) AS reincidentes,
                COUNT(*) AS total_com_pedido
            FROM (
                SELECT prospect_id, COUNT(*) AS cnt
                FROM orders
                GROUP BY prospect_id
            )
        """).fetchone()
        reincidentes = int(row["reincidentes"] or 0) if row else 0
        total_com_pedido = int(row["total_com_pedido"] or 0) if row else 0
        recompra_pct = round(reincidentes / total_com_pedido * 100, 1) if total_com_pedido else 0.0
    finally:
        conn.close()

    return {
        "periodo_dias": periodo_dias,
        "clientes_ativos": clientes_ativos,
        "vendas_periodo_brl": vendas_periodo_brl,
        "ticket_medio_brl": ticket_medio_brl,
        "pedidos_periodo": pedidos_periodo,
        "novos_clientes_periodo": novos_clientes_periodo,
        "recompra_pct": recompra_pct,
    }


def top_ltv(limit: int = 20) -> list[dict]:
    """Ranking pra UI + base da Custom Audience value-based do Meta (Sprint 2)."""
    return top_clientes_por_ltv(limit=limit)


def vendas_por_canal(periodo_dias: int = 30) -> list[dict]:
    """Agregação por canal de venda no período — usado pra fechar ROI por canal."""
    desde = (datetime.now() - timedelta(days=periodo_dias)).date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                COALESCE(canal, '(sem canal)') AS canal,
                COUNT(*) AS n_pedidos,
                COALESCE(SUM(valor_brl), 0) AS total_brl,
                COALESCE(AVG(valor_brl), 0) AS ticket_medio_brl
            FROM orders
            WHERE DATE(created_at) >= ?
            GROUP BY canal
            ORDER BY total_brl DESC
        """, (desde,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["total_brl"] = round(d["total_brl"] or 0, 2)
        d["ticket_medio_brl"] = round(d["ticket_medio_brl"] or 0, 2)
        out.append(d)
    return out


def listar_orders_cliente(username: str, plataforma: str = "instagram") -> list[dict]:
    """Histórico de pedidos de 1 cliente (lookup por username, não id)."""
    customer = get_customer_by_username(username, plataforma=plataforma)
    if not customer:
        return []
    return list_orders_by_prospect(customer["id"])
