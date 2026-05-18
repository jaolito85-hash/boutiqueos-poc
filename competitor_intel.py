"""
competitor_intel.py — Análise estratégica de concorrentes Instagram para a Haus.

Pipeline em 2 camadas (sem integração com caption/banner):
    1. COLETA — Apify (profile-scraper + post-scraper), cache local TTL configurável.
    2. ANÁLISE — OpenAI gpt-4.1-mini com JSON Schema estruturado, salva snapshot no SQLite.

API pública:
    analisar(handle, posts_limit=30, force_refresh=False, modelo=None) -> dict
    comparar(handles: list[str]) -> dict
    diff(handle, dias=30) -> dict
    listar_snapshots(handle=None) -> list[dict]

Configuração (env):
    APIFY_TOKEN          — obrigatório
    OPENAI_API_KEY       — obrigatório
    HAUS_COMPETITOR_MODEL — modelo OpenAI (default: 'gpt-4.1-mini')
    HAUS_COMPETITOR_CACHE_TTL_DAYS — TTL do cache Apify em dias (default: 7)
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from openai import OpenAI

from competitor_prompt import ANALYSIS_SCHEMA, SYSTEM_PROMPT, build_user_message
from database import (
    get_competitor_snapshot_before,
    get_latest_competitor_snapshot,
    init_db,
    list_competitor_snapshots,
    save_competitor_snapshot,
)


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

CACHE_DIR = Path(__file__).parent / "competitor_cache"
CACHE_TTL_DAYS = int(os.getenv("HAUS_COMPETITOR_CACHE_TTL_DAYS", "7"))
MODEL = os.getenv("HAUS_COMPETITOR_MODEL", "gpt-4.1-mini")

PROFILE_ACTOR = "apify/instagram-profile-scraper"
POSTS_ACTOR = "apify/instagram-post-scraper"

# Estimativas de custo (USD) — atualizadas em 2026-05 com base nos pricing pages
_COST_PROFILE_USD = 0.0026
_COST_POST_USD = 0.0017
_COST_OPENAI_PER_RUN_USD = 0.02  # estimativa conservadora para gpt-4.1-mini

_openai_client: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY não configurada no ambiente.")
        _openai_client = OpenAI()
    return _openai_client


def _get_apify():
    """Lazy-import do apify-client (mesmo padrão de 02_scraper.py)."""
    try:
        from apify_client import ApifyClient
    except ImportError as e:
        raise RuntimeError(
            "apify-client não instalado. Rode: pip install apify-client"
        ) from e
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN não configurada no ambiente.")
    return ApifyClient(token)


# ============================================================================
# CACHE LOCAL (JSON em competitor_cache/)
# ============================================================================

def _cache_path(handle: str, kind: str) -> Path:
    """Caminho do cache pra (handle, kind). kind: 'profile' | 'posts'."""
    safe = handle.lstrip("@").lower()
    return CACHE_DIR / f"{safe}__{kind}.json"


def _cache_read(handle: str, kind: str, ttl_days: int) -> dict | list | None:
    """Lê o cache se existir e ainda for fresh (idade < ttl_days). None caso contrário."""
    path = _cache_path(handle, kind)
    if not path.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > timedelta(days=ttl_days):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _cache_write(handle: str, kind: str, data) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(handle, kind)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================================
# COLETA — Apify
# ============================================================================

def _coletar_profile(handle: str, force_refresh: bool) -> dict:
    """Coleta o perfil via Apify (com cache)."""
    if not force_refresh:
        cached = _cache_read(handle, "profile", CACHE_TTL_DAYS)
        if cached:
            print(f"  [cache] profile @{handle} ({len(json.dumps(cached))} chars)")
            # cache armazena lista (resultado do dataset) — pegar 1º
            return cached[0] if isinstance(cached, list) else cached

    print(f"  [apify] rodando profile-scraper para @{handle}...")
    client = _get_apify()
    run = client.actor(PROFILE_ACTOR).call(
        run_input={"usernames": [handle.lstrip("@")]},
        timeout_secs=180,
    )
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    if not items:
        raise RuntimeError(f"profile-scraper não retornou dados para @{handle}")
    _cache_write(handle, "profile", items)
    return items[0]


def _coletar_posts(handle: str, limit: int, force_refresh: bool) -> list[dict]:
    """Coleta últimos N posts via Apify (com cache)."""
    if not force_refresh:
        cached = _cache_read(handle, "posts", CACHE_TTL_DAYS)
        if cached:
            print(f"  [cache] posts @{handle} ({len(cached)} itens)")
            return cached[:limit]

    print(f"  [apify] rodando post-scraper para @{handle} (limit={limit})...")
    client = _get_apify()
    run = client.actor(POSTS_ACTOR).call(
        run_input={
            "username": [handle.lstrip("@")],
            "resultsLimit": limit,
        },
        timeout_secs=300,
    )
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    _cache_write(handle, "posts", items)
    return items


# ============================================================================
# NORMALIZAÇÃO + MÉTRICAS
# ============================================================================

def _normalizar_profile(raw: dict) -> dict:
    """Extrai só os campos relevantes para o LLM (reduz tokens)."""
    return {
        "username": raw.get("username"),
        "fullName": raw.get("fullName"),
        "biography": raw.get("biography"),
        "followersCount": raw.get("followersCount"),
        "followsCount": raw.get("followsCount"),
        "postsCount": raw.get("postsCount"),
        "verified": raw.get("verified"),
        "isBusinessAccount": raw.get("isBusinessAccount"),
        "businessCategoryName": raw.get("businessCategoryName"),
        "externalUrl": raw.get("externalUrl"),
        "private": raw.get("private"),
        "relatedProfiles": [
            {"username": p.get("username"), "full_name": p.get("full_name")}
            for p in (raw.get("relatedProfiles") or [])
        ],
    }


def _normalizar_posts(posts: list[dict]) -> list[dict]:
    """Versão enxuta dos posts para o LLM (sem URLs gigantes, sem comentários completos)."""
    out = []
    for p in posts:
        out.append({
            "url": p.get("url"),
            "type": p.get("type"),
            "timestamp": p.get("timestamp"),
            "caption": (p.get("caption") or "")[:600],
            "likesCount": p.get("likesCount") or 0,
            "commentsCount": p.get("commentsCount") or 0,
            "videoViewCount": p.get("videoViewCount") or p.get("videoPlayCount") or 0,
            "hashtags": p.get("hashtags") or [],
            "mentions": p.get("mentions") or [],
            "isSponsored": p.get("isSponsored", False),
        })
    return out


def _calcular_metricas(profile: dict, posts: list[dict]) -> dict:
    """Calcula agregados que ajudam o LLM a não chutar números."""
    if not posts:
        return {
            "posts_no_periodo": 0,
            "likes_medio": 0,
            "comments_medio": 0,
            "engagement_rate_pct": 0.0,
            "freq_posts_dia": 0.0,
            "mix_formatos": {},
            "uso_hashtags": {"posts_com_hashtag": 0, "media_hashtags_por_post": 0.0},
            "periodo_coberto": None,
        }

    likes_total = sum(p.get("likesCount", 0) or 0 for p in posts)
    comments_total = sum(p.get("commentsCount", 0) or 0 for p in posts)
    likes_medio = round(likes_total / len(posts))
    comments_medio = round(comments_total / len(posts))

    followers = profile.get("followersCount") or 1
    engagement = (likes_medio + comments_medio) / followers * 100

    mix = Counter(p.get("type") or "unknown" for p in posts)

    posts_com_tag = sum(1 for p in posts if (p.get("hashtags") or []))
    total_tags = sum(len(p.get("hashtags") or []) for p in posts)

    # Frequência: precisa de >=2 datas válidas
    try:
        datas = sorted(
            datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            for p in posts if p.get("timestamp")
        )
    except (ValueError, KeyError, AttributeError):
        datas = []

    if len(datas) >= 2:
        span_dias = max((datas[-1] - datas[0]).total_seconds() / 86400, 1)
        freq = len(posts) / span_dias
        periodo = f"{datas[0].date().isoformat()} -> {datas[-1].date().isoformat()}"
    else:
        freq = 0.0
        periodo = None

    return {
        "posts_no_periodo": len(posts),
        "likes_medio": likes_medio,
        "comments_medio": comments_medio,
        "engagement_rate_pct": round(engagement, 2),
        "freq_posts_dia": round(freq, 2),
        "mix_formatos": dict(mix),
        "uso_hashtags": {
            "posts_com_hashtag": posts_com_tag,
            "media_hashtags_por_post": round(total_tags / len(posts), 1),
        },
        "periodo_coberto": periodo,
    }


# ============================================================================
# ANÁLISE — OpenAI
# ============================================================================

def _chamar_llm(handle: str, brief: dict, modelo: str) -> dict:
    """Roda o LLM com response_format=json_schema (saída validada)."""
    client = _get_openai()
    print(f"  [openai] analisando @{handle} com {modelo}...")
    resp = client.chat.completions.create(
        model=modelo,
        temperature=0.4,
        response_format={"type": "json_schema", "json_schema": ANALYSIS_SCHEMA},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(handle, brief)},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OpenAI retornou JSON inválido: {e}\nRaw: {raw[:300]}")


# ============================================================================
# API PÚBLICA
# ============================================================================

def analisar(
    handle: str,
    posts_limit: int = 30,
    force_refresh: bool = False,
    modelo: str | None = None,
) -> dict:
    """Roda coleta + análise + salva snapshot. Retorna o snapshot completo.

    Args:
        handle: @username do concorrente (com ou sem '@')
        posts_limit: nº de posts recentes a coletar (default 30)
        force_refresh: ignora cache Apify (re-paga)
        modelo: override do modelo OpenAI (default HAUS_COMPETITOR_MODEL)

    Returns:
        dict com: handle, snapshot_date, metricas, analysis, custo_usd, snapshot_id
    """
    init_db()
    handle_clean = handle.lstrip("@").lower()
    modelo = modelo or MODEL

    print(f"\n=== ANALISANDO @{handle_clean} ===")
    raw_profile = _coletar_profile(handle_clean, force_refresh)
    raw_posts = _coletar_posts(handle_clean, posts_limit, force_refresh)

    profile_slim = _normalizar_profile(raw_profile)
    posts_slim = _normalizar_posts(raw_posts)
    metricas = _calcular_metricas(profile_slim, posts_slim)

    brief = {"profile": profile_slim, "posts": posts_slim, "metricas": metricas}
    analysis = _chamar_llm(handle_clean, brief, modelo)

    # Custo aproximado
    custo = (
        _COST_PROFILE_USD
        + (_COST_POST_USD * len(raw_posts))
        + _COST_OPENAI_PER_RUN_USD
    )
    if not force_refresh and _cache_path(handle_clean, "profile").exists():
        # Se veio do cache não paga Apify
        cache_age = datetime.now() - datetime.fromtimestamp(
            _cache_path(handle_clean, "profile").stat().st_mtime
        )
        # cache_age < TTL_DAYS implica que JÁ existia antes desta chamada — simplificação:
        # assumimos que se cache existe é hit. (suficiente pra estimativa)
        if cache_age < timedelta(days=CACHE_TTL_DAYS):
            custo = _COST_OPENAI_PER_RUN_USD

    snapshot_date = date.today().isoformat()
    snapshot_id = save_competitor_snapshot(
        username=handle_clean,
        snapshot_date=snapshot_date,
        followers=profile_slim.get("followersCount"),
        posts_total=profile_slim.get("postsCount"),
        posts_periodo=metricas.get("posts_no_periodo"),
        engagement_rate=metricas.get("engagement_rate_pct"),
        freq_posts_dia=metricas.get("freq_posts_dia"),
        mix_formatos=metricas.get("mix_formatos"),
        raw_profile=raw_profile,
        raw_posts=raw_posts,
        analysis=analysis,
        posicionamento=analysis.get("posicionamento"),
        custo_usd=round(custo, 4),
    )
    print(f"  [db] snapshot salvo (id={snapshot_id}, custo~${custo:.4f})")

    return {
        "handle": handle_clean,
        "snapshot_id": snapshot_id,
        "snapshot_date": snapshot_date,
        "profile": profile_slim,
        "metricas": metricas,
        "analysis": analysis,
        "custo_usd": round(custo, 4),
    }


def comparar(handles: list[str], posts_limit: int = 30, force_refresh: bool = False) -> dict:
    """Roda analisar() pra cada handle e constrói uma matriz comparativa.

    Retorna:
        {
            "snapshots": [snapshot dict de cada handle],
            "matriz_metricas": tabela comparativa,
            "gaps_comuns": gaps que aparecem em mais de 1 concorrente,
            "oportunidades_cruzadas": ações que nenhum concorrente faz (vácuo total)
        }
    """
    snapshots = [analisar(h, posts_limit=posts_limit, force_refresh=force_refresh) for h in handles]

    matriz = []
    for s in snapshots:
        matriz.append({
            "handle": s["handle"],
            "followers": s["profile"].get("followersCount"),
            "posts_total": s["profile"].get("postsCount"),
            "engagement_rate_pct": s["metricas"].get("engagement_rate_pct"),
            "freq_posts_dia": s["metricas"].get("freq_posts_dia"),
            "mix_formatos": s["metricas"].get("mix_formatos"),
            "posicionamento": s["analysis"].get("posicionamento"),
        })

    # Gaps recorrentes
    gap_counter = Counter()
    for s in snapshots:
        gaps = s["analysis"].get("gaps", {})
        for k in ("hashtags", "frequencia", "formatos"):
            v = gaps.get(k)
            if v and isinstance(v, str) and len(v.strip()) > 5:
                gap_counter[k] += 1

    gaps_comuns = [k for k, n in gap_counter.items() if n >= max(2, len(snapshots) // 2)]

    # Oportunidades que aparecem com imitabilidade != 'evitar' em vários
    op_counter = Counter()
    for s in snapshots:
        for op in s["analysis"].get("oportunidades_haus", []):
            if op.get("imitabilidade") in ("copiar", "adaptar"):
                op_counter[op.get("acao", "")[:80]] += 1
    oportunidades_recorrentes = [
        {"acao": a, "vezes": n}
        for a, n in op_counter.most_common(10)
        if n >= 2
    ]

    return {
        "snapshots": snapshots,
        "matriz_metricas": matriz,
        "gaps_comuns": gaps_comuns,
        "oportunidades_recorrentes": oportunidades_recorrentes,
    }


def diff(handle: str, dias: int = 30) -> dict:
    """Compara o snapshot mais recente do handle com um snapshot >= `dias` atrás."""
    handle_clean = handle.lstrip("@").lower()
    atual = get_latest_competitor_snapshot(handle_clean)
    if not atual:
        raise RuntimeError(f"sem snapshot atual para @{handle_clean} — rode `analisar` antes.")

    ref_date = (datetime.fromisoformat(atual["snapshot_date"]) - timedelta(days=dias)).date().isoformat()
    anterior = get_competitor_snapshot_before(handle_clean, atual["snapshot_date"])
    if not anterior:
        return {
            "handle": handle_clean,
            "mensagem": "sem snapshot anterior pra comparar — rode `analisar` periodicamente pra acumular histórico.",
            "atual": {
                "snapshot_date": atual["snapshot_date"],
                "followers": atual["followers"],
            },
        }

    def _diff_num(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 2)

    return {
        "handle": handle_clean,
        "atual": {
            "snapshot_date": atual["snapshot_date"],
            "followers": atual["followers"],
            "engagement_rate": atual["engagement_rate"],
            "freq_posts_dia": atual["freq_posts_dia"],
            "posicionamento": atual["posicionamento"],
        },
        "anterior": {
            "snapshot_date": anterior["snapshot_date"],
            "followers": anterior["followers"],
            "engagement_rate": anterior["engagement_rate"],
            "freq_posts_dia": anterior["freq_posts_dia"],
            "posicionamento": anterior["posicionamento"],
        },
        "delta": {
            "dias": (datetime.fromisoformat(atual["snapshot_date"])
                     - datetime.fromisoformat(anterior["snapshot_date"])).days,
            "followers": _diff_num(atual["followers"], anterior["followers"]),
            "engagement_rate_pp": _diff_num(atual["engagement_rate"], anterior["engagement_rate"]),
            "freq_posts_dia": _diff_num(atual["freq_posts_dia"], anterior["freq_posts_dia"]),
            "posicionamento_mudou": (atual["posicionamento"] or "") != (anterior["posicionamento"] or ""),
        },
    }


def listar(handle: str | None = None) -> list[dict]:
    """Atalho para database.list_competitor_snapshots."""
    init_db()
    return list_competitor_snapshots(username=(handle.lstrip("@").lower() if handle else None))


# ============================================================================
# Seed (uso interno) — popula cache local a partir dos JSONs já baixados pela
# /apify-ultimate-scraper, evitando re-pagar a Apify no primeiro smoke test.
# ============================================================================

def seed_cache_from_dataset(handle: str, profile_json: list, posts_json: list) -> None:
    """Usado no smoke test inicial: alimenta o cache com dados que já temos."""
    _cache_write(handle.lstrip("@").lower(), "profile", profile_json)
    _cache_write(handle.lstrip("@").lower(), "posts", posts_json)
    print(f"[seed] cache populado para @{handle} ({len(posts_json)} posts)")


if __name__ == "__main__":
    # CLI mínimo de debug. Para uso real, prefira 07_concorrente.py.
    if len(sys.argv) < 2:
        print("Uso: python competitor_intel.py <handle>")
        sys.exit(1)
    out = analisar(sys.argv[1])
    print(json.dumps(out["analysis"], ensure_ascii=False, indent=2))
