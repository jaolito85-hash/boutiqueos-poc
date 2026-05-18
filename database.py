"""
Schema e helpers do SQLite pra prospects.
SQLite é o jeito mais simples de testar — quando virar produção,
migra pro Supabase com 1 comando (são os mesmos campos).

Uso:
    from database import init_db, save_prospect, get_pending_qualifications
    init_db()
"""

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "prospects.db"

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    source_loja TEXT,
    cidade_loja TEXT,

    -- dados brutos do scraper (Apify)
    raw_data TEXT,
    bio TEXT,
    seguidores INTEGER,
    seguindo INTEGER,
    eh_privado INTEGER DEFAULT 0,

    -- resultado da qualificação (agente IA)
    score INTEGER,
    confianca TEXT,
    razoes TEXT,
    mensagem TEXT,
    sinais TEXT,

    -- estados do funil
    status TEXT DEFAULT 'NEW',
    -- NEW: scraped, ainda nao qualificado
    -- QUALIFIED: passou pelo agente
    -- READY: aprovado, pronto pra Aline enviar
    -- REVIEW: revisar manualmente
    -- DISCARDED: descartado
    -- SENT: Aline enviou a DM
    -- REPLIED: a pessoa respondeu
    -- ENTERED_GROUP: entrou no grupo VIP

    -- timestamps
    scraped_at TIMESTAMP,
    qualified_at TIMESTAMP,
    sent_at TIMESTAMP,
    reply_at TIMESTAMP,

    -- proteção (rate limiting da Aline)
    sent_by_account TEXT,  -- qual conta IG enviou (pra futuro)
    daily_sent_date DATE   -- data do envio pra calcular limite diário
);

CREATE INDEX IF NOT EXISTS idx_status ON prospects(status);
CREATE INDEX IF NOT EXISTS idx_username ON prospects(username);
CREATE INDEX IF NOT EXISTS idx_score ON prospects(score);
CREATE INDEX IF NOT EXISTS idx_daily_sent ON prospects(daily_sent_date);

-- Tabela de eventos (auditoria, pra debug e métricas)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_username TEXT,
    event_type TEXT,   -- SCRAPED / QUALIFIED / SENT / REPLIED / etc
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_event_prospect ON events(prospect_username);

-- ============================================================================
-- CONTENT ENGINE (MVP) — catálogo de produtos + mídia
-- ============================================================================

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    categoria TEXT,
    -- porcelana / le_creuset / box_presente / orquidea / caixa_decorativa / outro
    faixa_preco TEXT,
    -- valor unitário em número (string para preservar formato — ex: "450" ou "1095.00")
    colecao TEXT,
    -- ex: "Linha Toile", "Coleção Verão 2026" — opcional, usado no banner IA
    descricao_breve TEXT,
    -- gerada por IA ou editada por Aline
    tags TEXT,
    -- JSON array de tags livres
    ativo INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_products_categoria ON products(categoria);
CREATE INDEX IF NOT EXISTS idx_products_ativo ON products(ativo);

CREATE TABLE IF NOT EXISTS product_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    -- raw: foto crua do celular
    -- processed: variação gerada por gpt-image ou template
    filepath TEXT NOT NULL,
    -- caminho relativo a partir da raiz do projeto (ex: media/raw/12/foto1.jpg)
    preset TEXT,
    -- variation / bg_swap / card (só para kind=processed)
    mode TEXT,
    -- editorial_photo / ai_banner / template_banner / retry_fix
    target_format TEXT,
    -- instagram_feed_quadrado / instagram_feed_retrato / instagram_story
    -- / whatsapp_status / whatsapp_post
    banner_payload TEXT,
    -- JSON: {price, collection, top_label, cta_top, cta_bottom, custom_prompt}
    prompt_used TEXT,
    -- prompt enviado à IA (só para kind=processed)
    source_media_id INTEGER,
    -- id da mídia que originou (raw ou outra processed)
    width INTEGER,
    height INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_media_product ON product_media(product_id);
CREATE INDEX IF NOT EXISTS idx_media_kind ON product_media(kind);
-- idx_media_mode é criado em _MIGRATIONS para suportar upgrade de banco antigo
"""

# Migrations para bancos já existentes (idempotente — captura OperationalError quando a coluna já existe)
_MIGRATIONS = [
    "ALTER TABLE products ADD COLUMN colecao TEXT",
    "ALTER TABLE product_media ADD COLUMN mode TEXT",
    "ALTER TABLE product_media ADD COLUMN target_format TEXT",
    "ALTER TABLE product_media ADD COLUMN banner_payload TEXT",
    "CREATE INDEX IF NOT EXISTS idx_media_mode ON product_media(mode)",
]


def _run_migrations(conn):
    """Aplica ALTERs idempotentes para bancos pré-existentes (Sprint 2)."""
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            # Coluna/índice já existe ou tabela ainda não — seguir adiante
            if "duplicate column name" not in str(e).lower() and "already exists" not in str(e).lower():
                # Erro inesperado: registra mas não interrompe (Sprint 1 ainda precisa rodar)
                print(f"[migration warn] {sql} → {e}")


def init_db():
    """Cria o banco e tabelas se não existirem, e aplica migrações Sprint 2."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _run_migrations(conn)
    conn.commit()
    conn.close()
    print(f"OK Banco inicializado em {DB_PATH}")


def get_connection():
    """Retorna uma conexão com row_factory pra acessar campos por nome."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# OPERAÇÕES — PROSPECTS
# ============================================================================

def save_prospect_raw(username: str, source_loja: str, cidade_loja: str, raw_data: dict):
    """Salva um perfil scrapeado, status NEW. Idempotente (UPDATE se já existe)."""
    bio = raw_data.get("biography", "")
    seguidores = raw_data.get("followersCount", 0)
    seguindo = raw_data.get("followsCount", 0)
    eh_privado = 1 if raw_data.get("private", False) else 0

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO prospects (
                username, source_loja, cidade_loja,
                raw_data, bio, seguidores, seguindo, eh_privado,
                status, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
            ON CONFLICT(username) DO UPDATE SET
                raw_data=excluded.raw_data,
                bio=excluded.bio,
                seguidores=excluded.seguidores,
                seguindo=excluded.seguindo,
                scraped_at=excluded.scraped_at
        """, (
            username, source_loja, cidade_loja,
            json.dumps(raw_data, ensure_ascii=False),
            bio, seguidores, seguindo, eh_privado,
            datetime.now().isoformat()
        ))
        log_event(conn, username, "SCRAPED", {"source": source_loja})
        conn.commit()
    finally:
        conn.close()


def get_pending_qualifications(limit=50):
    """Retorna perfis com status=NEW que precisam ser qualificados."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM prospects
            WHERE status = 'NEW'
            ORDER BY scraped_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_qualification(username: str, result: dict):
    """Salva o resultado da qualificação (vindo do agente IA)."""
    status_map = {
        "APROVAR": "READY",
        "REVISAR": "REVIEW",
        "DESCARTAR": "DISCARDED"
    }
    novo_status = status_map.get(result["status"], "REVIEW")

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE prospects SET
                score = ?, confianca = ?, razoes = ?,
                mensagem = ?, sinais = ?, status = ?,
                qualified_at = ?
            WHERE username = ?
        """, (
            result.get("score"),
            result.get("confianca"),
            json.dumps(result.get("razoes", []), ensure_ascii=False),
            result.get("mensagem"),
            json.dumps(result.get("sinais", []), ensure_ascii=False),
            novo_status,
            datetime.now().isoformat(),
            username
        ))
        log_event(conn, username, "QUALIFIED", {
            "score": result.get("score"),
            "status": novo_status
        })
        conn.commit()
    finally:
        conn.close()


def get_ready_queue(limit=30):
    """Fila pra Aline trabalhar: status READY ordenado por score DESC."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM prospects
            WHERE status = 'READY'
            ORDER BY score DESC, qualified_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_as_sent(username: str):
    """Aline confirma que enviou a DM."""
    conn = get_connection()
    try:
        now = datetime.now()
        conn.execute("""
            UPDATE prospects SET
                status = 'SENT',
                sent_at = ?,
                daily_sent_date = ?
            WHERE username = ?
        """, (now.isoformat(), now.date().isoformat(), username))
        log_event(conn, username, "SENT", {})
        conn.commit()
    finally:
        conn.close()


def mark_as_skipped(username: str):
    """Aline pula esse prospect (volta pra fila)."""
    conn = get_connection()
    try:
        log_event(conn, username, "SKIPPED", {})
        conn.commit()
    finally:
        conn.close()


def mark_as_not_client(username: str):
    """Aline marca como não-cliente (descartar permanente)."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE prospects SET status = 'DISCARDED' WHERE username = ?
        """, (username,))
        log_event(conn, username, "MANUALLY_DISCARDED", {})
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# PROTEÇÃO — KILL SWITCH E RATE LIMIT
# ============================================================================

DAILY_LIMIT = 15  # Protocolo de segurança: máx 15 DMs/dia

def get_sent_today():
    """Quantas DMs Aline já enviou hoje."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) as total FROM prospects
            WHERE daily_sent_date = ?
        """, (datetime.now().date().isoformat(),)).fetchone()
        return row["total"]
    finally:
        conn.close()


def can_send_more_today():
    """Retorna True se ainda pode enviar mais DMs hoje."""
    return get_sent_today() < DAILY_LIMIT


# ============================================================================
# EVENTOS / AUDITORIA
# ============================================================================

def log_event(conn, username: str, event_type: str, metadata: dict):
    """Loga evento sem commit (deixa pro chamador)."""
    conn.execute("""
        INSERT INTO events (prospect_username, event_type, metadata)
        VALUES (?, ?, ?)
    """, (username, event_type, json.dumps(metadata, ensure_ascii=False)))


# ============================================================================
# ESTATÍSTICAS — pra dashboard
# ============================================================================

def get_stats():
    """Estatísticas gerais do funil."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT status, COUNT(*) as total FROM prospects GROUP BY status
        """).fetchall()
        stats = {r["status"]: r["total"] for r in rows}
        stats["sent_today"] = get_sent_today()
        stats["can_send_more"] = can_send_more_today()
        stats["daily_limit"] = DAILY_LIMIT
        return stats
    finally:
        conn.close()


# ============================================================================
# CONTENT ENGINE — Produtos
# ============================================================================

def create_product(nome: str, categoria: str = None, faixa_preco: str = None,
                   descricao_breve: str = None, tags: list = None,
                   colecao: str = None) -> int:
    """Cria um produto novo. Retorna o id criado."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO products (nome, categoria, faixa_preco, descricao_breve, tags, colecao)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            nome, categoria, faixa_preco, descricao_breve,
            json.dumps(tags or [], ensure_ascii=False),
            colecao,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_product(product_id: int, **fields):
    """Atualiza campos do produto. Aceita: nome, categoria, faixa_preco, colecao, descricao_breve, tags, ativo."""
    permitidos = {"nome", "categoria", "faixa_preco", "colecao", "descricao_breve", "tags", "ativo"}
    fields = {k: v for k, v in fields.items() if k in permitidos}
    if not fields:
        return
    if "tags" in fields and isinstance(fields["tags"], list):
        fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in fields.keys())
    values = list(fields.values()) + [datetime.now().isoformat(), product_id]
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE products SET {sets}, updated_at=? WHERE id=?",
            values
        )
        conn.commit()
    finally:
        conn.close()


def get_product(product_id: int) -> dict | None:
    """Busca um produto + suas mídias."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not row:
            return None
        produto = dict(row)
        produto["tags"] = json.loads(produto["tags"]) if produto.get("tags") else []
        produto["media"] = [
            dict(m) for m in conn.execute(
                "SELECT * FROM product_media WHERE product_id=? ORDER BY created_at ASC",
                (product_id,)
            ).fetchall()
        ]
        return produto
    finally:
        conn.close()


def list_products(ativo_apenas: bool = True) -> list[dict]:
    """Lista produtos. Por padrão apenas ativos."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM products"
        params = ()
        if ativo_apenas:
            sql += " WHERE ativo=1"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        produtos = []
        for r in rows:
            p = dict(r)
            p["tags"] = json.loads(p["tags"]) if p.get("tags") else []
            # contar mídias
            counts = conn.execute("""
                SELECT kind, COUNT(*) as n FROM product_media
                WHERE product_id=? GROUP BY kind
            """, (p["id"],)).fetchall()
            p["media_counts"] = {c["kind"]: c["n"] for c in counts}
            produtos.append(p)
        return produtos
    finally:
        conn.close()


# ============================================================================
# CONTENT ENGINE — Mídia (raw + processed)
# ============================================================================

def add_product_media(product_id: int, kind: str, filepath: str,
                      preset: str = None, prompt_used: str = None,
                      source_media_id: int = None,
                      width: int = None, height: int = None,
                      mode: str = None, target_format: str = None,
                      banner_payload: dict = None) -> int:
    """
    Registra uma mídia (raw ou processed) no banco.
    kind: 'raw' | 'processed'
    filepath: relativo à raiz do projeto (ex: 'media/raw/12/foto1.jpg')
    mode: editorial_photo | ai_banner | template_banner | retry_fix (só para processed)
    target_format: instagram_feed_quadrado | ... (só para processed)
    banner_payload: dict com {price, collection, top_label, cta_top, cta_bottom, custom_prompt}
    """
    if kind not in ("raw", "processed"):
        raise ValueError(f"kind inválido: {kind}")
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO product_media
                (product_id, kind, filepath, preset, prompt_used, source_media_id,
                 width, height, mode, target_format, banner_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            product_id, kind, filepath, preset, prompt_used, source_media_id,
            width, height, mode, target_format,
            json.dumps(banner_payload, ensure_ascii=False) if banner_payload else None,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_product_media(media_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM product_media WHERE id=?", (media_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_product_media(product_id: int, kind: str = None) -> list[dict]:
    """Lista mídias de um produto. kind opcional para filtrar."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM product_media WHERE product_id=?"
        params = [product_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY created_at ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_product_media(media_id: int):
    """Remove uma mídia do banco. Arquivo no disco deve ser removido pelo chamador."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM product_media WHERE id=?", (media_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# CLI básico
# ============================================================================

if __name__ == "__main__":
    init_db()
    stats = get_stats()
    print("\n=== ESTATÍSTICAS ATUAIS ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    produtos = list_products(ativo_apenas=False)
    print(f"\n=== CATÁLOGO ===")
    print(f"  produtos cadastrados: {len(produtos)}")
