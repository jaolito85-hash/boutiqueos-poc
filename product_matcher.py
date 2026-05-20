"""
product_matcher.py — Sugere produto do catálogo pra cada lead pós-CSV.

Premissa: o lead comentou em vídeo de terceiros ("qual o valor dessa panela?",
"quero a vela", etc). O matcher tenta identificar a categoria/produto da Haus
mais alinhado ao comentário e oferece pra Aline anexar o banner correspondente
na DM.

Algoritmo (simples, transparente):
    1. Normaliza o texto do lead (lower + remove acentos).
    2. Pra cada produto ATIVO do catálogo, calcula um score baseado em:
        - keywords da categoria que aparecem no texto
        - palavras significativas do nome do produto que aparecem no texto
        - palavras da coleção do produto que aparecem no texto
        - palavras da descrição_breve
    3. Empate: produto com banner gerado (ai_banner ou processed) ganha.
    4. Sem match (score == 0): retorna None — UI mostra "sem sugestão".

Sem IA, sem custo de API. Tudo regex/string puro.

API pública:
    match_product_for_lead(texto: str) -> dict | None
        Retorna {"product": {...}, "match_score": int, "matched_keywords": [...],
                 "best_media": {"id", "filepath", "url", "kind", "mode"} | None}
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from database import get_connection


# ============================================================================
# KEYWORDS POR CATEGORIA
# ============================================================================
# Cada categoria do catálogo tem uma lista de palavras-chave que costumam
# aparecer no comentário do lead. Lista mantida manualmente — pode crescer
# conforme observamos novos padrões nos comentários.

CATEGORIA_KEYWORDS: dict[str, list[str]] = {
    "le_creuset": [
        "panela", "panelas", "le creuset", "lecreuset", "lê creuset",
        "cocotte", "cocotte le", "ferro fundido", "dutch oven",
        "wicked", "abobora", "moedor", "moedor de sal", "moedor de pimenta",
        "esmaltada", "frigideira",
    ],
    "porcelana": [
        "xicara", "xicaras", "porcelana", "jogo de cha", "jogo de chá",
        "mesa posta", "mesaposta", "prato", "pratos", "taca", "tacas",
        "aparelho de jantar", "louca", "loucas", "toile", "azul e branco",
        "vermelho e branco", "sopeira", "bule", "cristal", "cristais",
    ],
    "box_presente": [
        "box", "box presente", "kit", "combo", "presente", "presentear",
        "vela", "velas", "perfume", "difusor", "fragrancia", "fragrancias",
        "l'occitane", "loccitane", "lenvie", "dani fernandes",
        "aniversario", "dia das maes", "dia das mães", "natal",
    ],
    "orquidea": [
        "orquidea", "orquidias", "flor", "flores", "vaso", "vasos", "planta",
    ],
    "caixa_decorativa": [
        "caixa", "caixinha", "decoracao", "decor", "organizador",
        "porta-joias", "porta joias",
    ],
    # 'outro' não tem keywords automáticas — só matcha via nome/coleção/descrição
    "outro": [],
}


# Palavras descartadas no matching por nome/coleção/descrição
STOPWORDS_MATCH = {
    "de", "da", "do", "das", "dos", "em", "no", "na", "nas", "nos",
    "para", "pra", "com", "sem", "e", "ou", "o", "a", "os", "as",
    "um", "uma", "uns", "umas", "que", "qual", "quais", "se", "ao",
    "à", "às", "aos", "por", "pelo", "pela", "este", "esta", "esse",
    "essa", "aquele", "aquela", "haus",
}


# ============================================================================
# NORMALIZAÇÃO
# ============================================================================

def _strip_accents(s: str) -> str:
    """'Maringá' -> 'Maringa'. Útil pra comparar strings em pt-BR."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return _strip_accents(s.lower())


def _significant_words(s: str | None) -> list[str]:
    """Palavras significativas (lower, sem acento, len>=4, não-stopword)."""
    if not s:
        return []
    norm = _normalize(s)
    raw = re.findall(r"[a-z0-9]+", norm)
    return [w for w in raw if len(w) >= 4 and w not in STOPWORDS_MATCH]


# ============================================================================
# CACHE DE CATÁLOGO
# ============================================================================
# Recarrega o catálogo a cada N segundos pra evitar query a cada lead.
# Cache simples por TTL — invalidação manual via reload_catalog_cache().

_CACHE: dict = {"loaded_at": 0.0, "products": []}
_CACHE_TTL_SEC = 60


def _load_active_products() -> list[dict]:
    """Carrega produtos ativos com sua melhor mídia.

    Best media = primeiro 'processed/ai_banner' → 'processed' qualquer →
    'raw'. Se o produto não tem mídia, best_media é None.
    """
    conn = get_connection()
    try:
        prods = conn.execute(
            "SELECT id, nome, categoria, faixa_preco, colecao, descricao_breve, tags "
            "FROM products WHERE ativo = 1"
        ).fetchall()
        out = []
        for p in prods:
            d = dict(p)
            # buscar melhor media
            best = conn.execute("""
                SELECT id, filepath, kind, mode
                FROM product_media
                WHERE product_id = ?
                ORDER BY
                    CASE WHEN kind='processed' AND mode='ai_banner' THEN 0
                         WHEN kind='processed' THEN 1
                         WHEN kind='raw' THEN 2
                         ELSE 3 END,
                    created_at DESC
                LIMIT 1
            """, (d["id"],)).fetchone()
            if best:
                bm = dict(best)
                bm["url"] = "/" + bm["filepath"].replace("\\", "/")
                d["best_media"] = bm
            else:
                d["best_media"] = None
            out.append(d)
        return out
    finally:
        conn.close()


def _get_products() -> list[dict]:
    import time
    if time.time() - _CACHE["loaded_at"] > _CACHE_TTL_SEC:
        _CACHE["products"] = _load_active_products()
        _CACHE["loaded_at"] = time.time()
    return _CACHE["products"]


def reload_catalog_cache() -> None:
    """Force-reload do cache. Útil em testes ou após CRUD de produtos."""
    _CACHE["loaded_at"] = 0.0
    _get_products()


# ============================================================================
# MATCHING
# ============================================================================

def _product_score(produto: dict, texto_norm: str) -> tuple[int, list[str]]:
    """Calcula score e lista de keywords matched pra um produto."""
    hits: list[str] = []
    cat = (produto.get("categoria") or "").lower()

    # Keywords da categoria (peso 2 — sinal mais forte)
    for kw in CATEGORIA_KEYWORDS.get(cat, []):
        kw_norm = _normalize(kw)
        if kw_norm and kw_norm in texto_norm:
            hits.append(kw)

    cat_score = len(hits) * 2

    # Palavras do nome / coleção / descrição (peso 1 — sinal mais fraco)
    extras: list[str] = []
    for campo in ("nome", "colecao", "descricao_breve"):
        for w in _significant_words(produto.get(campo) or ""):
            if w in texto_norm:
                extras.append(w)

    extra_score = len(set(extras))
    score = cat_score + extra_score
    return score, hits + list(dict.fromkeys(extras))  # dedupe preservando ordem


def match_product_for_lead(texto: str | None) -> dict | None:
    """Retorna a melhor sugestão de produto pro texto do lead, ou None.

    Returns:
        {
            "product": {"id", "nome", "categoria", "faixa_preco", "colecao", ...},
            "match_score": int,        # quão forte foi o match
            "matched_keywords": [str], # quais palavras casaram (debug/UI)
            "best_media": {"id", "filepath", "url", "kind", "mode"} | None,
        }
        ou None se nenhum produto tem score > 0.
    """
    if not texto or not texto.strip():
        return None

    texto_norm = _normalize(texto)
    if not texto_norm:
        return None

    melhor: dict | None = None
    melhor_score = 0

    for prod in _get_products():
        score, hits = _product_score(prod, texto_norm)
        if score == 0:
            continue
        # Empate: prefere produto com banner (ai_banner > processed > raw > nenhum)
        if score > melhor_score or (
            score == melhor_score and melhor and
            _media_quality(prod.get("best_media")) > _media_quality(melhor["product"].get("best_media"))
        ):
            melhor_score = score
            melhor = {
                "product": prod,
                "match_score": score,
                "matched_keywords": hits,
                "best_media": prod.get("best_media"),
            }

    return melhor


def _media_quality(media: dict | None) -> int:
    """Ordena qualidade de mídia: ai_banner > processed > raw > nenhum."""
    if not media:
        return 0
    if media.get("kind") == "processed" and media.get("mode") == "ai_banner":
        return 3
    if media.get("kind") == "processed":
        return 2
    if media.get("kind") == "raw":
        return 1
    return 0


# ============================================================================
# Helpers para serialização compacta na API
# ============================================================================

def extract_text_for_matching(prospect_or_serialized: dict) -> str:
    """Concatena os campos textuais úteis pra matching de produto.

    Aceita tanto o dict cru de prospects (com raw_data como string JSON) quanto
    a versão já serializada (com raw_data como dict e campos derivados como
    'evidencia', 'texto_original' direto no topo).
    """
    if not prospect_or_serialized:
        return ""
    p = prospect_or_serialized
    parts: list[str] = []

    # raw_data pode ser dict (serializado) ou string JSON (cru)
    rd = p.get("raw_data")
    if isinstance(rd, str) and rd:
        try:
            import json as _json
            rd = _json.loads(rd)
        except Exception:
            rd = {}
    if not isinstance(rd, dict):
        rd = {}

    for v in (
        p.get("evidencia") or rd.get("evidencia"),
        p.get("texto_original") or rd.get("texto_original"),
        p.get("bio"),
    ):
        if v and isinstance(v, str) and v.strip():
            parts.append(v.strip())

    return " | ".join(parts)


def serialize_suggestion(suggestion: dict | None) -> dict | None:
    """Versão compacta pra anexar em listagens (queue/review/followup)."""
    if not suggestion:
        return None
    p = suggestion["product"]
    bm = suggestion.get("best_media")
    return {
        "product_id": p["id"],
        "nome": p.get("nome"),
        "categoria": p.get("categoria"),
        "faixa_preco": p.get("faixa_preco"),
        "colecao": p.get("colecao"),
        "match_score": suggestion["match_score"],
        "matched_keywords": suggestion["matched_keywords"][:6],
        "thumb_url": (bm or {}).get("url"),
        "media_id": (bm or {}).get("id"),
        "media_kind": (bm or {}).get("kind"),
        "media_mode": (bm or {}).get("mode"),
    }
