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
from datetime import datetime, timedelta
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
    -- CUSTOMER: virou cliente confirmado (Aline marcou compra)

    -- timestamps
    scraped_at TIMESTAMP,
    qualified_at TIMESTAMP,
    sent_at TIMESTAMP,
    reply_at TIMESTAMP,
    entered_group_at TIMESTAMP,
    customer_at TIMESTAMP,

    -- conversão final
    valor_compra REAL,  -- valor da compra que efetivou o lead (opcional)

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
-- idx_sent_at, idx_reply_at, idx_entered_group_at, idx_customer_at criados em _MIGRATIONS
-- (essas colunas podem não existir em bancos antigos antes do ALTER TABLE rodar).

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

-- Registro de cada execução do analisar (mesmo handle pode ser re-analisado várias vezes
-- no mesmo dia; competitor_snapshots faz UPSERT por dia, então não conta execuções).
-- Esta tabela é a fonte da verdade pra throttle diário de uso.
CREATE TABLE IF NOT EXISTS competitor_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    plataforma TEXT NOT NULL DEFAULT 'instagram',
    run_date DATE NOT NULL,
    custo_usd REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comp_runs_date ON competitor_runs(run_date);

-- ============================================================================
-- VENDAS — histórico de pedidos por cliente (suporta recompra)
-- ============================================================================
-- prospects.valor_compra guarda a 1ª compra (compatibilidade). Esta tabela
-- guarda TODAS as compras (1:N com prospect) — fonte de verdade pra LTV, ROAS,
-- audiência value-based no Meta Ads.

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL,
    valor_brl REAL NOT NULL,
    canal TEXT,                               -- whatsapp_dm | grupo_vip | loja_fisica | meta_ad | organico_outro
    utm_source TEXT,                          -- preenchido se veio via link rastreado
    utm_campaign TEXT,
    produtos_json TEXT,                       -- snapshot leve: [{nome, preco}]
    notas TEXT,                               -- nota livre da Aline
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orders_prospect ON orders(prospect_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_canal ON orders(canal);

-- ============================================================================
-- CONTENT IDEAS — Fase 2 #3: oportunidades do competitor viram backlog de posts
-- ============================================================================
-- Cada ideia nasce de uma `oportunidade_haus` da análise de concorrente.
-- Aline aprova/usa/descarta. Quando vira produto, linka via product_id.

CREATE TABLE IF NOT EXISTS content_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_handle TEXT NOT NULL,
    source_plataforma TEXT NOT NULL DEFAULT 'instagram',
    acao TEXT NOT NULL,
    imitabilidade TEXT,
    esforco TEXT,
    racional TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    product_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP,
    UNIQUE(source_handle, acao)
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON content_ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_source ON content_ideas(source_handle);

-- ============================================================================
-- SENT MESSAGES — Fase 2 #4: snapshot pra ranking de templates que convertem
-- ============================================================================
-- Cada vez que a Aline marca "enviei", guardamos:
--   - snapshot exato do texto que saiu (sem editar mesmo se prospect mudar depois)
--   - template_origem inferido (perguntando_preco / buscando_atendimento /
--     perguntando_local / parceria_competitor / agente_ia)
--   - se a Aline editou o template antes de enviar
-- Quando o lead vira REPLIED/ENTERED_GROUP/CUSTOMER, marcamos outcome.
-- Permite ranking: "templates que mais convertem".

CREATE TABLE IF NOT EXISTS sent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL,
    mensagem TEXT NOT NULL,
    template_origem TEXT,
    foi_editada INTEGER DEFAULT 0,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    outcome TEXT,            -- replied | entered_group | customer (preenchido depois)
    outcome_at TIMESTAMP,
    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sm_prospect ON sent_messages(prospect_id);
CREATE INDEX IF NOT EXISTS idx_sm_template ON sent_messages(template_origem);
CREATE INDEX IF NOT EXISTS idx_sm_outcome ON sent_messages(outcome);
CREATE INDEX IF NOT EXISTS idx_sm_sent_at ON sent_messages(sent_at);
"""

# Migrations para bancos já existentes (idempotente — captura OperationalError quando a coluna já existe)
_MIGRATIONS = [
    "ALTER TABLE products ADD COLUMN colecao TEXT",
    "ALTER TABLE product_media ADD COLUMN mode TEXT",
    "ALTER TABLE product_media ADD COLUMN target_format TEXT",
    "ALTER TABLE product_media ADD COLUMN banner_payload TEXT",
    "CREATE INDEX IF NOT EXISTS idx_media_mode ON product_media(mode)",
    # Sprint 4 — tracking de conversão pós-envio
    "ALTER TABLE prospects ADD COLUMN entered_group_at TIMESTAMP",
    "ALTER TABLE prospects ADD COLUMN customer_at TIMESTAMP",
    "ALTER TABLE prospects ADD COLUMN valor_compra REAL",
    "CREATE INDEX IF NOT EXISTS idx_sent_at ON prospects(sent_at)",
    "CREATE INDEX IF NOT EXISTS idx_reply_at ON prospects(reply_at)",
    "CREATE INDEX IF NOT EXISTS idx_entered_group_at ON prospects(entered_group_at)",
    "CREATE INDEX IF NOT EXISTS idx_customer_at ON prospects(customer_at)",
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
    valid = {"NEW", "QUALIFIED", "READY", "REVIEW", "DISCARDED", "SENT", "REPLIED", "ENTERED_GROUP", "CUSTOMER"}
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
    valid = {"NEW", "QUALIFIED", "READY", "REVIEW", "DISCARDED", "SENT", "REPLIED", "ENTERED_GROUP", "CUSTOMER"}
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


def _derive_template_origin(sinais_raw, source_loja: str | None) -> str:
    """Inferir qual template foi a base da mensagem enviada.

    Convenções:
      - parceria_competitor → lead vindo de oportunidade de concorrente
      - perguntando_preco / buscando_atendimento / perguntando_local → CSV import
      - agente_ia → lead vindo do scraper Apify + agente IA
    """
    sinais: list = []
    if isinstance(sinais_raw, str) and sinais_raw:
        try:
            sinais = json.loads(sinais_raw)
        except Exception:
            sinais = []
    elif isinstance(sinais_raw, list):
        sinais = sinais_raw
    if "parceria_potencial" in sinais:
        return "parceria_competitor"
    for s in sinais:
        if isinstance(s, str) and s.startswith("intent_"):
            intent = s[len("intent_"):]
            if intent in ("perguntando_preco", "buscando_atendimento", "perguntando_local"):
                return intent
    # fallback — outbound automatizado via scraper + IA
    if source_loja and not source_loja.startswith("competitor:") and source_loja != "csv_import":
        return "agente_ia"
    return "outro"


def save_sent_message(prospect_id: int, mensagem: str, template_origem: str,
                      foi_editada: bool = False) -> int:
    """Salva o snapshot da mensagem que saiu (Fase 2 #4)."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO sent_messages
                (prospect_id, mensagem, template_origem, foi_editada, sent_at)
            VALUES (?, ?, ?, ?, ?)
        """, (prospect_id, mensagem, template_origem,
              1 if foi_editada else 0, datetime.now().isoformat()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_outcome_for_prospect(prospect_id: int, outcome: str) -> int:
    """Marca outcome na ÚLTIMA sent_message do prospect que ainda não tem.

    outcome ∈ replied | entered_group | customer. Aceita sobrescrever
    pra outcome 'maior' (replied → customer faz sentido).
    """
    if outcome not in ("replied", "entered_group", "customer"):
        raise ValueError(f"outcome inválido: {outcome}")
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT id, outcome FROM sent_messages
            WHERE prospect_id = ?
            ORDER BY sent_at DESC LIMIT 1
        """, (prospect_id,)).fetchone()
        if not row:
            return 0
        # outcome rank — só sobrescreve se o novo for "maior"
        rank = {None: 0, "replied": 1, "entered_group": 2, "customer": 3}
        if rank.get(outcome, 0) <= rank.get(row["outcome"], 0):
            return 0
        cur = conn.execute("""
            UPDATE sent_messages
               SET outcome = ?, outcome_at = ?
             WHERE id = ?
        """, (outcome, datetime.now().isoformat(), row["id"]))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_as_sent(username: str, mensagem_enviada: str | None = None,
                 plataforma: str = "instagram") -> bool:
    """Aline confirma que enviou a DM.

    Se `mensagem_enviada` for fornecida, salva snapshot em sent_messages
    e marca foi_editada se difere da `prospects.mensagem` original.
    """
    conn = get_connection()
    try:
        # Pega contexto do prospect pra inferir template_origem e detectar edição
        row = conn.execute("""
            SELECT id, mensagem, sinais, source_loja
            FROM prospects WHERE plataforma = ? AND username = ?
        """, (plataforma, username)).fetchone()
        if not row:
            return False
        now = datetime.now()
        conn.execute("""
            UPDATE prospects SET
                status = 'SENT',
                sent_at = ?,
                daily_sent_date = ?
            WHERE id = ?
        """, (now.isoformat(), now.date().isoformat(), row["id"]))
        log_event(conn, username, "SENT", {"plataforma": plataforma})
        conn.commit()
    finally:
        conn.close()

    # Snapshot fora do bloco anterior (precisa de get_connection limpo)
    if mensagem_enviada is not None and mensagem_enviada.strip():
        template = _derive_template_origin(row["sinais"], row["source_loja"])
        foi_editada = (mensagem_enviada.strip() != (row["mensagem"] or "").strip())
        save_sent_message(row["id"], mensagem_enviada.strip(), template, foi_editada)

    return True


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
# CONVERSÃO PÓS-ENVIO — tracking de respondeu / entrou no grupo / comprou
# ============================================================================
# Plataforma é opcional (default 'instagram') por compat com chamadas legadas
# vindas do painel atual, que ainda envia só o username. Pra leads do TikTok
# vindos do CSV, o front passa plataforma explicitamente.

def _get_prospect_id(plataforma: str, username: str) -> int | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM prospects WHERE plataforma=? AND username=?",
            (plataforma, username)
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def mark_as_replied(username: str, plataforma: str = "instagram") -> bool:
    """SENT → REPLIED. Aceita pular status (REVIEW/READY) — Aline pode marcar
    direto se a pessoa respondeu antes de a DM ser registrada como enviada."""
    conn = get_connection()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute("""
            UPDATE prospects SET status = 'REPLIED', reply_at = ?
            WHERE plataforma = ? AND username = ?
        """, (now, plataforma, username))
        if cur.rowcount:
            log_event(conn, username, "REPLIED", {"plataforma": plataforma})
            conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok:
        pid = _get_prospect_id(plataforma, username)
        if pid:
            update_outcome_for_prospect(pid, "replied")
    return ok


def mark_as_entered_group(username: str, plataforma: str = "instagram") -> bool:
    """REPLIED/SENT → ENTERED_GROUP. Marca entrada no grupo VIP."""
    conn = get_connection()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute("""
            UPDATE prospects SET status = 'ENTERED_GROUP', entered_group_at = ?
            WHERE plataforma = ? AND username = ?
        """, (now, plataforma, username))
        if cur.rowcount:
            log_event(conn, username, "ENTERED_GROUP", {"plataforma": plataforma})
            conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok:
        pid = _get_prospect_id(plataforma, username)
        if pid:
            update_outcome_for_prospect(pid, "entered_group")
    return ok


def mark_as_customer(username: str, plataforma: str = "instagram",
                     valor: float | None = None) -> bool:
    """Qualquer status → CUSTOMER. Salva valor opcional da compra."""
    conn = get_connection()
    try:
        now = datetime.now().isoformat()
        cur = conn.execute("""
            UPDATE prospects SET
                status = 'CUSTOMER',
                customer_at = ?,
                valor_compra = COALESCE(?, valor_compra)
            WHERE plataforma = ? AND username = ?
        """, (now, valor, plataforma, username))
        if cur.rowcount:
            log_event(conn, username, "BECAME_CUSTOMER",
                      {"plataforma": plataforma, "valor": valor})
            conn.commit()
        ok = cur.rowcount > 0
    finally:
        conn.close()
    if ok:
        pid = _get_prospect_id(plataforma, username)
        if pid:
            update_outcome_for_prospect(pid, "customer")
    return ok


# ============================================================================
# FOLLOWUP — listagem paginada de leads pós-envio (aba "Acompanhar")
# ============================================================================

_FOLLOWUP_STATUSES = ("SENT", "REPLIED", "ENTERED_GROUP", "CUSTOMER")

def list_followup_prospects(
    *,
    statuses: list[str] | None = None,
    plataforma: str | None = None,
    source_loja: str | None = None,
    days: int | None = None,
    busca: str | None = None,
    sort: str = "sent_recent",
    page: int = 1,
    page_size: int = 30,
) -> dict:
    """Lista leads em status pós-envio. Filtros e ordenação.

    Args:
        statuses: subset de SENT/REPLIED/ENTERED_GROUP/CUSTOMER (default: todos)
        plataforma: 'instagram' | 'tiktok' | None
        source_loja: filtra exato (ex: 'degustacasa')
        days: janela em dias contando de sent_at (None = tudo)
        busca: substring em username/source_loja
        sort: 'sent_recent' | 'sent_oldest' | 'value_desc'

    Returns:
        {"items": [...], "total": int, "page": int, "page_size": int, "pages": int}
    """
    if statuses:
        statuses = [s for s in statuses if s in _FOLLOWUP_STATUSES]
    if not statuses:
        statuses = list(_FOLLOWUP_STATUSES)
    placeholders = ",".join("?" * len(statuses))

    where = [f"status IN ({placeholders})"]
    params: list = list(statuses)

    if plataforma in ("instagram", "tiktok"):
        where.append("plataforma = ?")
        params.append(plataforma)
    if source_loja:
        where.append("source_loja = ?")
        params.append(source_loja)
    if days and days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        where.append("sent_at >= ?")
        params.append(cutoff)
    if busca:
        like = f"%{busca.lower()}%"
        where.append("(LOWER(username) LIKE ? OR LOWER(IFNULL(source_loja,'')) LIKE ?)")
        params.extend([like, like])

    order_map = {
        "sent_recent": "sent_at DESC",
        "sent_oldest": "sent_at ASC",
        "value_desc": "valor_compra DESC NULLS LAST, sent_at DESC",
    }
    order_by = order_map.get(sort, order_map["sent_recent"])

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


# ============================================================================
# INSIGHTS — agregações para o dashboard
# ============================================================================
# Todas as queries são read-only e SQL puro. Performance suficiente até
# centenas de milhares de prospects (índices em status, sent_at, etc).

def funnel_counts() -> dict:
    """Contagem por status, na ordem do funil."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM prospects GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
    finally:
        conn.close()
    # ordem canônica
    ordered = [
        "NEW", "QUALIFIED", "REVIEW", "READY",
        "SENT", "REPLIED", "ENTERED_GROUP", "CUSTOMER", "DISCARDED",
    ]
    return {s: by_status.get(s, 0) for s in ordered}


def conversion_rates() -> dict:
    """Taxas globais: reply/group/customer sobre o total SENT (cumulativo)."""
    f = funnel_counts()
    # SENT cumulativo = leads que pelo menos chegaram a sair como DM
    sent_total = f["SENT"] + f["REPLIED"] + f["ENTERED_GROUP"] + f["CUSTOMER"]
    replied_total = f["REPLIED"] + f["ENTERED_GROUP"] + f["CUSTOMER"]
    group_total = f["ENTERED_GROUP"] + f["CUSTOMER"]
    customer_total = f["CUSTOMER"]

    def _rate(n, d):
        return round(n / d, 4) if d else 0.0

    # Tempo médio até resposta (em horas), para os que responderam
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT AVG((julianday(reply_at) - julianday(sent_at)) * 24.0) AS avg_h
            FROM prospects
            WHERE sent_at IS NOT NULL AND reply_at IS NOT NULL
        """).fetchone()
        avg_reply_hours = round(row["avg_h"], 2) if row and row["avg_h"] is not None else None
    finally:
        conn.close()

    return {
        "sent_total": sent_total,
        "reply_rate": _rate(replied_total, sent_total),
        "group_rate": _rate(group_total, sent_total),
        "customer_rate": _rate(customer_total, sent_total),
        "avg_reply_hours": avg_reply_hours,
    }


def conversion_by_source(limit: int = 10) -> list[dict]:
    """Agregação por source_loja: quantos SENT, quantos REPLIED+, quantos CUSTOMER."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                COALESCE(source_loja, '(sem fonte)') AS source_loja,
                SUM(CASE WHEN status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status IN ('REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS replied,
                SUM(CASE WHEN status IN ('ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS group_in,
                SUM(CASE WHEN status = 'CUSTOMER' THEN 1 ELSE 0 END) AS customer
            FROM prospects
            WHERE status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER')
            GROUP BY source_loja
            ORDER BY sent DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["reply_rate"] = round(d["replied"] / d["sent"], 4) if d["sent"] else 0.0
        d["customer_rate"] = round(d["customer"] / d["sent"], 4) if d["sent"] else 0.0
        out.append(d)
    return out


def conversion_by_intent() -> list[dict]:
    """Agregação por intent extraído de sinais (intent_perguntando_preco etc)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN sinais LIKE '%intent_perguntando_preco%'   THEN 'perguntando_preco'
                    WHEN sinais LIKE '%intent_buscando_atendimento%' THEN 'buscando_atendimento'
                    WHEN sinais LIKE '%intent_perguntando_local%'    THEN 'perguntando_local'
                    ELSE '(sem intent)'
                END AS intent,
                SUM(CASE WHEN status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status IN ('REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS replied,
                SUM(CASE WHEN status = 'CUSTOMER' THEN 1 ELSE 0 END) AS customer
            FROM prospects
            WHERE status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER')
            GROUP BY intent
            ORDER BY sent DESC
        """).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["reply_rate"] = round(d["replied"] / d["sent"], 4) if d["sent"] else 0.0
        out.append(d)
    return out


def conversion_by_platform() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                plataforma,
                SUM(CASE WHEN status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status IN ('REPLIED','ENTERED_GROUP','CUSTOMER') THEN 1 ELSE 0 END) AS replied,
                SUM(CASE WHEN status = 'CUSTOMER' THEN 1 ELSE 0 END) AS customer
            FROM prospects
            WHERE status IN ('SENT','REPLIED','ENTERED_GROUP','CUSTOMER')
            GROUP BY plataforma
            ORDER BY sent DESC
        """).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["reply_rate"] = round(d["replied"] / d["sent"], 4) if d["sent"] else 0.0
        out.append(d)
    return out


def activity_timeseries(days: int = 30) -> list[dict]:
    """Atividade diária dos últimos N dias.

    Retorna lista de {date, sent, replied, group_in, customer}.
    Inclui dias com zero (preenche gaps) pra gráfico contínuo.
    """
    today = datetime.now().date()
    start = today - timedelta(days=days - 1)

    conn = get_connection()
    try:
        sent_rows = conn.execute("""
            SELECT DATE(sent_at) AS d, COUNT(*) AS n
            FROM prospects
            WHERE sent_at IS NOT NULL AND DATE(sent_at) >= ?
            GROUP BY DATE(sent_at)
        """, (start.isoformat(),)).fetchall()
        rep_rows = conn.execute("""
            SELECT DATE(reply_at) AS d, COUNT(*) AS n
            FROM prospects
            WHERE reply_at IS NOT NULL AND DATE(reply_at) >= ?
            GROUP BY DATE(reply_at)
        """, (start.isoformat(),)).fetchall()
        grp_rows = conn.execute("""
            SELECT DATE(entered_group_at) AS d, COUNT(*) AS n
            FROM prospects
            WHERE entered_group_at IS NOT NULL AND DATE(entered_group_at) >= ?
            GROUP BY DATE(entered_group_at)
        """, (start.isoformat(),)).fetchall()
        cus_rows = conn.execute("""
            SELECT DATE(customer_at) AS d, COUNT(*) AS n
            FROM prospects
            WHERE customer_at IS NOT NULL AND DATE(customer_at) >= ?
            GROUP BY DATE(customer_at)
        """, (start.isoformat(),)).fetchall()
    finally:
        conn.close()

    sent_map = {r["d"]: r["n"] for r in sent_rows}
    rep_map = {r["d"]: r["n"] for r in rep_rows}
    grp_map = {r["d"]: r["n"] for r in grp_rows}
    cus_map = {r["d"]: r["n"] for r in cus_rows}

    series = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        series.append({
            "date": d,
            "sent": sent_map.get(d, 0),
            "replied": rep_map.get(d, 0),
            "group_in": grp_map.get(d, 0),
            "customer": cus_map.get(d, 0),
        })
    return series


def template_ranking(min_sent: int = 1) -> list[dict]:
    """Ranking de templates por taxa de conversão.

    Retorna [{template_origem, sent, replied, entered_group, customer,
              reply_rate, group_rate, customer_rate, foi_editada_pct}]
    ordenado por customer_rate DESC, depois reply_rate DESC.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                COALESCE(template_origem, '(sem template)') AS template_origem,
                COUNT(*) AS sent,
                SUM(CASE WHEN outcome IN ('replied','entered_group','customer') THEN 1 ELSE 0 END) AS replied,
                SUM(CASE WHEN outcome IN ('entered_group','customer') THEN 1 ELSE 0 END) AS entered_group,
                SUM(CASE WHEN outcome = 'customer' THEN 1 ELSE 0 END) AS customer,
                AVG(CASE WHEN foi_editada = 1 THEN 1.0 ELSE 0.0 END) AS edit_rate
            FROM sent_messages
            GROUP BY template_origem
            HAVING sent >= ?
        """, (min_sent,)).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        sent = d["sent"] or 1
        d["reply_rate"] = round(d["replied"] / sent, 4)
        d["group_rate"] = round(d["entered_group"] / sent, 4)
        d["customer_rate"] = round(d["customer"] / sent, 4)
        d["foi_editada_pct"] = round((d["edit_rate"] or 0) * 100, 1)
        del d["edit_rate"]
        out.append(d)
    out.sort(key=lambda x: (-x["customer_rate"], -x["reply_rate"], -x["sent"]))
    return out


# ============================================================================
# VENDAS — orders (recompra) + LTV
# ============================================================================
# A 1ª compra também atualiza prospects.valor_compra / customer_at via
# mark_as_customer (já existente). Esta tabela armazena TODAS as compras.

def create_order(
    prospect_id: int,
    valor_brl: float,
    *,
    canal: str | None = None,
    utm_source: str | None = None,
    utm_campaign: str | None = None,
    produtos: list[dict] | None = None,
    notas: str | None = None,
) -> int:
    """Registra uma compra. Retorna o id criado."""
    if valor_brl is None or valor_brl < 0:
        raise ValueError("valor_brl precisa ser >= 0")
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO orders (
                prospect_id, valor_brl, canal, utm_source, utm_campaign,
                produtos_json, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            prospect_id, float(valor_brl), canal, utm_source, utm_campaign,
            json.dumps(produtos, ensure_ascii=False) if produtos else None,
            notas,
        ))
        conn.commit()
        # Log no events pra trilha de auditoria, igual outros marcos
        log_event(conn, _get_prospect_username(conn, prospect_id) or f"id_{prospect_id}",
                  "ORDER_CREATED",
                  {"valor_brl": float(valor_brl), "canal": canal, "order_id": cur.lastrowid})
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _get_prospect_username(conn, prospect_id: int) -> str | None:
    row = conn.execute(
        "SELECT username FROM prospects WHERE id = ?", (prospect_id,)
    ).fetchone()
    return row["username"] if row else None


def list_orders_by_prospect(prospect_id: int) -> list[dict]:
    """Histórico de pedidos do cliente, mais recente primeiro."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM orders WHERE prospect_id = ?
            ORDER BY created_at DESC, id DESC
        """, (prospect_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("produtos_json"):
            try:
                d["produtos"] = json.loads(d["produtos_json"])
            except (ValueError, TypeError):
                d["produtos"] = []
        else:
            d["produtos"] = []
        out.append(d)
    return out


def customer_summary(prospect_id: int) -> dict:
    """Resumo derivado de orders pra 1 cliente: LTV, ticket médio, dias desde
    última compra. Útil pra cards na aba 'Vendas' e value-based audience na Meta."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total_pedidos,
                COALESCE(SUM(valor_brl), 0) AS lifetime_value_brl,
                COALESCE(AVG(valor_brl), 0) AS ticket_medio_brl,
                MIN(created_at) AS primeira_compra,
                MAX(created_at) AS ultima_compra
            FROM orders
            WHERE prospect_id = ?
        """, (prospect_id,)).fetchone()
    finally:
        conn.close()

    if not row or row["total_pedidos"] == 0:
        return {
            "total_pedidos": 0,
            "lifetime_value_brl": 0.0,
            "ticket_medio_brl": 0.0,
            "primeira_compra": None,
            "ultima_compra": None,
            "dias_desde_ultima": None,
        }

    d = dict(row)
    d["lifetime_value_brl"] = round(d["lifetime_value_brl"] or 0, 2)
    d["ticket_medio_brl"] = round(d["ticket_medio_brl"] or 0, 2)
    if d.get("ultima_compra"):
        try:
            ult = datetime.fromisoformat(d["ultima_compra"])
            d["dias_desde_ultima"] = (datetime.now() - ult).days
        except (ValueError, TypeError):
            d["dias_desde_ultima"] = None
    else:
        d["dias_desde_ultima"] = None
    return d


def top_clientes_por_ltv(limit: int = 20) -> list[dict]:
    """Top clientes por LTV (soma de orders.valor_brl). Base da Custom Audience Meta."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT
                p.id AS prospect_id,
                p.username,
                p.plataforma,
                p.cidade_loja,
                p.source_loja,
                p.customer_at,
                SUM(o.valor_brl) AS lifetime_value_brl,
                COUNT(o.id) AS total_pedidos,
                AVG(o.valor_brl) AS ticket_medio_brl,
                MAX(o.created_at) AS ultima_compra
            FROM prospects p
            JOIN orders o ON o.prospect_id = p.id
            GROUP BY p.id
            ORDER BY lifetime_value_brl DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["lifetime_value_brl"] = round(d["lifetime_value_brl"] or 0, 2)
        d["ticket_medio_brl"] = round(d["ticket_medio_brl"] or 0, 2)
        out.append(d)
    return out


def list_clientes(limit: int = 200, busca: str | None = None) -> list[dict]:
    """Lista clientes (prospects com status='CUSTOMER' OU com >=1 order),
    incluindo LTV/ticket médio derivados. Ordena por última compra desc."""
    conn = get_connection()
    try:
        sql = """
            SELECT
                p.id AS prospect_id,
                p.username,
                p.plataforma,
                p.cidade_loja,
                p.source_loja,
                p.status,
                p.customer_at,
                p.valor_compra,
                COALESCE(SUM(o.valor_brl), p.valor_compra, 0) AS lifetime_value_brl,
                COUNT(o.id) AS total_pedidos,
                MAX(o.created_at) AS ultima_compra
            FROM prospects p
            LEFT JOIN orders o ON o.prospect_id = p.id
            WHERE p.status = 'CUSTOMER' OR o.id IS NOT NULL
        """
        params: list = []
        if busca:
            sql += " AND (p.username LIKE ? OR p.source_loja LIKE ?)"
            like = f"%{busca}%"
            params.extend([like, like])
        sql += """
            GROUP BY p.id
            ORDER BY ultima_compra DESC NULLS LAST, p.customer_at DESC NULLS LAST
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["lifetime_value_brl"] = round(d["lifetime_value_brl"] or 0, 2)
        out.append(d)
    return out


def get_customer_by_username(username: str, plataforma: str = "instagram") -> dict | None:
    """Busca o prospect/cliente + summary derivado pra modal de detalhe."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT id, username, plataforma, source_loja, cidade_loja,
                   status, customer_at, valor_compra, sent_at, reply_at,
                   entered_group_at, bio, seguidores
            FROM prospects
            WHERE plataforma = ? AND username = ?
        """, (plataforma, username)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["summary"] = customer_summary(d["id"])
    d["orders"] = list_orders_by_prospect(d["id"])
    return d


# ============================================================================
# CONTENT IDEAS — Fase 2 #3
# ============================================================================
# Cada ideia nasce de uma `oportunidade_haus` da análise de concorrente.
# Aline pode salvar/dispensar/converter-em-produto.

def save_content_idea(
    *,
    source_handle: str,
    acao: str,
    imitabilidade: str | None = None,
    esforco: str | None = None,
    racional: str | None = None,
    source_plataforma: str = "instagram",
) -> str:
    """Salva uma ideia. Idempotente — duplicata (source_handle, acao) retorna 'duplicada'."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO content_ideas (
                source_handle, source_plataforma, acao, imitabilidade,
                esforco, racional, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(source_handle, acao) DO NOTHING
        """, (source_handle, source_plataforma, acao, imitabilidade,
              esforco, racional))
        inserted = cur.rowcount > 0
        conn.commit()
        return "inserida" if inserted else "duplicada"
    finally:
        conn.close()


def list_content_ideas(status: str = "pending", limit: int = 50) -> list[dict]:
    """Lista ideias por status. status='all' traz tudo."""
    conn = get_connection()
    try:
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM content_ideas ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM content_ideas WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_pending_ideas() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM content_ideas WHERE status = 'pending'"
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def mark_idea_used(idea_id: int, product_id: int) -> bool:
    """Marca ideia como usada e linka pro produto criado."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            UPDATE content_ideas
               SET status = 'used', product_id = ?, used_at = ?
             WHERE id = ? AND status = 'pending'
        """, (product_id, datetime.now().isoformat(), idea_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_idea_dismissed(idea_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE content_ideas SET status = 'dismissed' "
            "WHERE id = ? AND status = 'pending'",
            (idea_id,)
        )
        conn.commit()
        return cur.rowcount > 0
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


def log_competitor_run(
    username: str, plataforma: str = "instagram", custo_usd: float | None = None
) -> int:
    """Registra 1 execução do analisar (usado pelo throttle diário)."""
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO competitor_runs (username, plataforma, run_date, custo_usd)
            VALUES (?, ?, DATE('now', 'localtime'), ?)
        """, (username, plataforma, custo_usd))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def count_competitor_runs_today() -> int:
    """Conta execuções do analisar feitas hoje (data local)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) AS n FROM competitor_runs
            WHERE run_date = DATE('now', 'localtime')
        """).fetchone()
        return int(row["n"] or 0)
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
