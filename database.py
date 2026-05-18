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
    username TEXT NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'instagram',
    external_id TEXT,
    source_loja TEXT,
    cidade_loja TEXT,

    -- dados brutos do scraper (Apify) ou do CSV importado
    raw_data TEXT,
    bio TEXT,
    seguidores INTEGER,
    seguindo INTEGER,
    eh_privado INTEGER DEFAULT 0,

    -- resultado da qualificação (agente IA ou score externo do CSV)
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
    daily_sent_date DATE,  -- data do envio pra calcular limite diário

    UNIQUE(plataforma, username)
);

CREATE INDEX IF NOT EXISTS idx_status ON prospects(status);
CREATE INDEX IF NOT EXISTS idx_username ON prospects(username);
CREATE INDEX IF NOT EXISTS idx_plat_user ON prospects(plataforma, username);
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

-- ============================================================================
-- COMPETITOR INTEL — snapshots de análise de concorrentes
-- ============================================================================

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'instagram',
    snapshot_date DATE NOT NULL,

    -- métricas calculadas (resumo rápido sem precisar parsear raw)
    followers INTEGER,
    posts_total INTEGER,
    posts_periodo INTEGER,         -- nº de posts no período coletado
    engagement_rate REAL,          -- %
    freq_posts_dia REAL,
    mix_formatos TEXT,             -- JSON {"Video": 14, "Sidecar": 14, "Image": 2}

    -- payloads originais (para re-análise futura sem re-pagar Apify)
    raw_profile TEXT,              -- JSON do Apify profile-scraper
    raw_posts TEXT,                -- JSON array do Apify post-scraper

    -- saída estruturada do LLM
    analysis TEXT,                 -- JSON: posicionamento, gaps, oportunidades_haus, etc
    posicionamento TEXT,           -- string curta extraída de analysis (atalho p/ queries)

    -- contabilidade
    custo_usd REAL,                -- soma estimada Apify + OpenAI
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(plataforma, username, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_comp_username ON competitor_snapshots(username);
CREATE INDEX IF NOT EXISTS idx_comp_date ON competitor_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_comp_plat_user ON competitor_snapshots(plataforma, username);
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


def _migrate_prospects_schema(conn):
    """Sprint 3: recria a tabela prospects pra ter (plataforma, username) UNIQUE.
    SQLite não suporta DROP CONSTRAINT, então a forma segura é rename+create+copy.
    Idempotente: sai cedo se já tem as colunas novas."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()}
    if not cols:
        return  # tabela ainda não existe — SCHEMA acima já criou no formato novo
    if "plataforma" in cols and "external_id" in cols:
        return  # já migrada

    print("[migration] recriando tabela prospects com UNIQUE(plataforma, username)...")
    conn.executescript("""
        BEGIN;
        ALTER TABLE prospects RENAME TO _prospects_old;
        CREATE TABLE prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            plataforma TEXT NOT NULL DEFAULT 'instagram',
            external_id TEXT,
            source_loja TEXT,
            cidade_loja TEXT,
            raw_data TEXT,
            bio TEXT,
            seguidores INTEGER,
            seguindo INTEGER,
            eh_privado INTEGER DEFAULT 0,
            score INTEGER,
            confianca TEXT,
            razoes TEXT,
            mensagem TEXT,
            sinais TEXT,
            status TEXT DEFAULT 'NEW',
            scraped_at TIMESTAMP,
            qualified_at TIMESTAMP,
            sent_at TIMESTAMP,
            reply_at TIMESTAMP,
            sent_by_account TEXT,
            daily_sent_date DATE,
            UNIQUE(plataforma, username)
        );
        INSERT INTO prospects (
            id, username, plataforma, external_id, source_loja, cidade_loja,
            raw_data, bio, seguidores, seguindo, eh_privado,
            score, confianca, razoes, mensagem, sinais,
            status, scraped_at, qualified_at, sent_at, reply_at,
            sent_by_account, daily_sent_date
        )
        SELECT
            id, username, 'instagram', NULL, source_loja, cidade_loja,
            raw_data, bio, seguidores, seguindo, eh_privado,
            score, confianca, razoes, mensagem, sinais,
            status, scraped_at, qualified_at, sent_at, reply_at,
            sent_by_account, daily_sent_date
        FROM _prospects_old;
        DROP TABLE _prospects_old;
        CREATE INDEX IF NOT EXISTS idx_status ON prospects(status);
        CREATE INDEX IF NOT EXISTS idx_username ON prospects(username);
        CREATE INDEX IF NOT EXISTS idx_plat_user ON prospects(plataforma, username);
        CREATE INDEX IF NOT EXISTS idx_score ON prospects(score);
        CREATE INDEX IF NOT EXISTS idx_daily_sent ON prospects(daily_sent_date);
        COMMIT;
    """)


def init_db():
    """Cria o banco e tabelas se não existirem, e aplica migrações Sprint 2 + Sprint 3.

    Ordem importa:
      1. Migration de prospects roda primeiro (recria tabela antiga sem 'plataforma' se necessário).
      2. SCHEMA cria tabelas ausentes (banco vazio) e índices que referenciam 'plataforma'.
      3. Migrations idempotentes do Sprint 2 (ALTERs em products/product_media).
    """
    conn = sqlite3.connect(DB_PATH)
    _migrate_prospects_schema(conn)
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

def save_prospect_raw(username: str, source_loja: str, cidade_loja: str, raw_data: dict,
                      plataforma: str = "instagram"):
    """Salva um perfil scrapeado, status NEW. Idempotente (UPDATE se já existe).
    Dedup composto: (plataforma, username) — IG @x e TikTok @x são leads diferentes."""
    bio = raw_data.get("biography", "")
    seguidores = raw_data.get("followersCount", 0)
    seguindo = raw_data.get("followsCount", 0)
    eh_privado = 1 if raw_data.get("private", False) else 0

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO prospects (
                username, plataforma, source_loja, cidade_loja,
                raw_data, bio, seguidores, seguindo, eh_privado,
                status, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
            ON CONFLICT(plataforma, username) DO UPDATE SET
                raw_data=excluded.raw_data,
                bio=excluded.bio,
                seguidores=excluded.seguidores,
                seguindo=excluded.seguindo,
                scraped_at=excluded.scraped_at
        """, (
            username, plataforma, source_loja, cidade_loja,
            json.dumps(raw_data, ensure_ascii=False),
            bio, seguidores, seguindo, eh_privado,
            datetime.now().isoformat()
        ))
        log_event(conn, username, "SCRAPED", {"source": source_loja, "plataforma": plataforma})
        conn.commit()
    finally:
        conn.close()


def prospect_exists(plataforma: str, username: str) -> bool:
    """True se já existe prospect com (plataforma, username) no banco."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM prospects WHERE plataforma=? AND username=? LIMIT 1",
            (plataforma, username)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_imported_prospect(
    plataforma: str,
    username: str,
    *,
    external_id: str | None = None,
    source_loja: str = "csv_import",
    cidade_loja: str | None = None,
    score: int | None = None,
    confianca: str = "media",
    razoes: list | None = None,
    mensagem: str | None = None,
    sinais: list | None = None,
    raw_data: dict | None = None,
    status: str = "REVIEW",
) -> str:
    """Insere um lead vindo do CSV. Retorna 'inserido' | 'duplicado'.

    Diferente de save_prospect_raw, este usa ON CONFLICT DO NOTHING:
    não sobrescreve leads existentes (importação não pode atropelar dados do Apify).
    """
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO prospects (
                username, plataforma, external_id, source_loja, cidade_loja,
                raw_data, score, confianca, razoes, mensagem, sinais,
                status, scraped_at, qualified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plataforma, username) DO NOTHING
        """, (
            username, plataforma, external_id, source_loja, cidade_loja,
            json.dumps(raw_data or {}, ensure_ascii=False),
            score, confianca,
            json.dumps(razoes or [], ensure_ascii=False),
            mensagem,
            json.dumps(sinais or [], ensure_ascii=False),
            status, now, now,
        ))
        inserted = cur.rowcount > 0
        if inserted:
            log_event(conn, username, "IMPORTED_CSV", {
                "plataforma": plataforma,
                "external_id": external_id,
                "score": score,
                "status": status,
            })
        conn.commit()
        return "inserido" if inserted else "duplicado"
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


def list_review_prospects(
    *,
    plataforma: str | None = None,
    temperatura: str | None = None,
    intent: str | None = None,
    busca: str | None = None,
    sort: str = "score_desc",
    page: int = 1,
    page_size: int = 30,
) -> dict:
    """Lista paginada de leads em REVIEW com filtros e ordenação.

    Args:
        plataforma: 'instagram' | 'tiktok' | None (todos)
        temperatura: 'quente' | 'morno' | None — usa sinais (temp_quente, temp_morno)
        intent: 'perguntando_preco' | 'buscando_atendimento' | 'perguntando_local' | None
        busca: substring case-insensitive em username/source_loja
        sort: 'score_desc' | 'score_asc' | 'recent' | 'oldest'
        page: 1-indexed
        page_size: máx 100

    Returns:
        {"items": [...], "total": int, "page": int, "page_size": int, "pages": int}
    """
    where = ["status = 'REVIEW'"]
    params: list = []

    if plataforma in ("instagram", "tiktok"):
        where.append("plataforma = ?")
        params.append(plataforma)
    if temperatura in ("quente", "morno", "frio"):
        where.append("sinais LIKE ?")
        params.append(f"%temp_{temperatura}%")
    if intent in ("perguntando_preco", "buscando_atendimento", "perguntando_local"):
        where.append("sinais LIKE ?")
        params.append(f"%intent_{intent}%")
    if busca:
        like = f"%{busca.lower()}%"
        where.append("(LOWER(username) LIKE ? OR LOWER(IFNULL(source_loja,'')) LIKE ?)")
        params.extend([like, like])

    order_map = {
        "score_desc": "score DESC, qualified_at DESC",
        "score_asc": "score ASC, qualified_at DESC",
        "recent": "qualified_at DESC",
        "oldest": "qualified_at ASC",
    }
    order_by = order_map.get(sort, order_map["score_desc"])

    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    offset = (page - 1) * page_size

    where_sql = " AND ".join(where)
    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM prospects WHERE {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM prospects WHERE {where_sql} "
            f"ORDER BY {order_by} LIMIT ? OFFSET ?",
            (*params, page_size, offset)
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()

    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def get_prospect_by_id(prospect_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM prospects WHERE id=?", (prospect_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_prospect_status(prospect_id: int, novo_status: str, *, event_type: str | None = None) -> dict | None:
    """Muda status do prospect. Retorna o prospect atualizado ou None se não existe."""
    valid = {"NEW", "QUALIFIED", "READY", "REVIEW", "DISCARDED", "SENT", "REPLIED", "ENTERED_GROUP"}
    if novo_status not in valid:
        raise ValueError(f"status inválido: {novo_status}")
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE prospects SET status=? WHERE id=?",
            (novo_status, prospect_id)
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT username FROM prospects WHERE id=?", (prospect_id,)).fetchone()
        if row and event_type:
            log_event(conn, row["username"], event_type, {"new_status": novo_status})
        conn.commit()
    finally:
        conn.close()
    return get_prospect_by_id(prospect_id)


def bulk_set_status(prospect_ids: list[int], novo_status: str, *, event_type: str | None = None) -> int:
    """Aplica novo status a uma lista de IDs. Retorna quantos foram atualizados."""
    if not prospect_ids:
        return 0
    valid = {"NEW", "QUALIFIED", "READY", "REVIEW", "DISCARDED", "SENT", "REPLIED", "ENTERED_GROUP"}
    if novo_status not in valid:
        raise ValueError(f"status inválido: {novo_status}")
    placeholders = ",".join("?" * len(prospect_ids))
    conn = get_connection()
    try:
        # log antes do update pra preservar usernames
        if event_type:
            rows = conn.execute(
                f"SELECT username FROM prospects WHERE id IN ({placeholders})",
                prospect_ids
            ).fetchall()
            for r in rows:
                log_event(conn, r["username"], event_type, {"new_status": novo_status, "bulk": True})
        cur = conn.execute(
            f"UPDATE prospects SET status=? WHERE id IN ({placeholders})",
            (novo_status, *prospect_ids)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_prospect_message(prospect_id: int, mensagem: str) -> bool:
    """Atualiza apenas o campo mensagem de um prospect. Retorna True se afetou linha."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE prospects SET mensagem=? WHERE id=?",
            (mensagem, prospect_id)
        )
        if cur.rowcount:
            row = conn.execute("SELECT username FROM prospects WHERE id=?", (prospect_id,)).fetchone()
            if row:
                log_event(conn, row["username"], "MESSAGE_EDITED", {"length": len(mensagem or "")})
            conn.commit()
        return cur.rowcount > 0
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
# COMPETITOR INTEL — snapshots de análise
# ============================================================================

def save_competitor_snapshot(
    username: str,
    snapshot_date: str,
    *,
    plataforma: str = "instagram",
    followers: int | None = None,
    posts_total: int | None = None,
    posts_periodo: int | None = None,
    engagement_rate: float | None = None,
    freq_posts_dia: float | None = None,
    mix_formatos: dict | None = None,
    raw_profile: dict | None = None,
    raw_posts: list | None = None,
    analysis: dict | None = None,
    posicionamento: str | None = None,
    custo_usd: float | None = None,
) -> int:
    """Salva (ou substitui) um snapshot de análise de concorrente.

    snapshot_date: 'YYYY-MM-DD'. Mesmo (plataforma, username, snapshot_date) =
    UPSERT — re-rodar no mesmo dia atualiza o registro.
    """
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO competitor_snapshots (
                username, plataforma, snapshot_date,
                followers, posts_total, posts_periodo,
                engagement_rate, freq_posts_dia, mix_formatos,
                raw_profile, raw_posts, analysis,
                posicionamento, custo_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plataforma, username, snapshot_date) DO UPDATE SET
                followers=excluded.followers,
                posts_total=excluded.posts_total,
                posts_periodo=excluded.posts_periodo,
                engagement_rate=excluded.engagement_rate,
                freq_posts_dia=excluded.freq_posts_dia,
                mix_formatos=excluded.mix_formatos,
                raw_profile=excluded.raw_profile,
                raw_posts=excluded.raw_posts,
                analysis=excluded.analysis,
                posicionamento=excluded.posicionamento,
                custo_usd=excluded.custo_usd
        """, (
            username, plataforma, snapshot_date,
            followers, posts_total, posts_periodo,
            engagement_rate, freq_posts_dia,
            json.dumps(mix_formatos, ensure_ascii=False) if mix_formatos is not None else None,
            json.dumps(raw_profile, ensure_ascii=False) if raw_profile is not None else None,
            json.dumps(raw_posts, ensure_ascii=False) if raw_posts is not None else None,
            json.dumps(analysis, ensure_ascii=False) if analysis is not None else None,
            posicionamento, custo_usd,
        ))
        conn.commit()
        # cur.lastrowid não funciona em UPSERT que disparou UPDATE — buscar id real
        row = conn.execute(
            "SELECT id FROM competitor_snapshots WHERE plataforma=? AND username=? AND snapshot_date=?",
            (plataforma, username, snapshot_date),
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


def _hydrate_snapshot(row) -> dict:
    """Converte uma row em dict, desserializando os campos JSON."""
    if row is None:
        return None
    d = dict(row)
    for k in ("mix_formatos", "raw_profile", "raw_posts", "analysis"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def get_latest_competitor_snapshot(username: str, plataforma: str = "instagram") -> dict | None:
    """Retorna o snapshot mais recente do handle (ou None)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM competitor_snapshots
            WHERE plataforma=? AND username=?
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
        """, (plataforma, username)).fetchone()
        return _hydrate_snapshot(row)
    finally:
        conn.close()


def get_competitor_snapshot_by_date(
    username: str, snapshot_date: str, plataforma: str = "instagram"
) -> dict | None:
    """Retorna o snapshot exato da data (YYYY-MM-DD)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM competitor_snapshots
            WHERE plataforma=? AND username=? AND snapshot_date=?
        """, (plataforma, username, snapshot_date)).fetchone()
        return _hydrate_snapshot(row)
    finally:
        conn.close()


def get_competitor_snapshot_before(
    username: str, ref_date: str, plataforma: str = "instagram"
) -> dict | None:
    """Retorna o snapshot mais recente com snapshot_date < ref_date (YYYY-MM-DD).
    Usado para o diff temporal: queremos o último antes da data atual."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM competitor_snapshots
            WHERE plataforma=? AND username=? AND snapshot_date < ?
            ORDER BY snapshot_date DESC, id DESC
            LIMIT 1
        """, (plataforma, username, ref_date)).fetchone()
        return _hydrate_snapshot(row)
    finally:
        conn.close()


def list_competitor_snapshots(
    username: str | None = None, plataforma: str = "instagram", limit: int = 100
) -> list[dict]:
    """Lista snapshots (de um handle específico ou todos). Resumo sem raw/analysis."""
    conn = get_connection()
    try:
        sql = """
            SELECT id, username, plataforma, snapshot_date, followers, posts_total,
                   posts_periodo, engagement_rate, freq_posts_dia, posicionamento,
                   custo_usd, created_at
            FROM competitor_snapshots
        """
        params = []
        if username:
            sql += " WHERE plataforma=? AND username=?"
            params = [plataforma, username]
        sql += " ORDER BY snapshot_date DESC, username ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
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
