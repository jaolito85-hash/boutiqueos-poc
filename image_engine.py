"""
image_engine.py — Wrapper OpenAI para tratamento de fotos de produto da haus.

Configuração via env vars:
    OPENAI_API_KEY      — chave da API
    HAUS_IMAGE_MODEL    — ID do modelo (default: 'gpt-image-1')
                          troque se a OpenAI lançar versão nova (ex: 'gpt-image-2')

3 presets brand-aligned:
    - variation:  mesma cena, fundo neutro/clean alternativo
    - bg_swap:    troca de fundo bagunçado por cena premium escolhida
    - card:       card de produto isolado em fundo limpo

API principal:
    tratar_foto(media_id, presets=["variation", "bg_swap"], n_por_preset=2)
        → lista[novos_media_ids]
"""

import os
import base64
import time
import re
from pathlib import Path
from typing import Literal

from openai import OpenAI

from database import (
    get_product,
    get_product_media,
    add_product_media,
)
from catalogo import PROCESSED_DIR

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

ROOT = Path(__file__).parent
IMAGE_MODEL = os.getenv("HAUS_IMAGE_MODEL", "gpt-image-1")
DEFAULT_QUALITY = os.getenv("HAUS_IMAGE_QUALITY", "high")
DEFAULT_TARGET = os.getenv("HAUS_DEFAULT_TARGET", "instagram_feed_quadrado")

Preset = Literal["variation", "bg_swap", "card"]
TargetFormat = Literal[
    "instagram_feed_quadrado",
    "instagram_feed_retrato",
    "instagram_story",
    "whatsapp_status",
    "whatsapp_post",
]

# Mapeamento target → (size que pedimos à OpenAI, tamanho final em pixels, descrição visual)
# OpenAI gpt-image-1 aceita: 1024x1024, 1024x1536, 1536x1024 (e "auto")
TARGET_FORMATS: dict[str, dict] = {
    "instagram_feed_quadrado": {
        "openai": "1024x1024",
        "final": (1080, 1080),
        "aspect_desc": "square 1:1",
    },
    "instagram_feed_retrato": {
        "openai": "1024x1536",  # 2:3 — cortado para 4:5
        "final": (1080, 1350),
        "aspect_desc": "portrait 4:5",
    },
    "instagram_story": {
        "openai": "1024x1536",  # 2:3 — cortado para 9:16
        "final": (1080, 1920),
        "aspect_desc": "vertical 9:16",
    },
    "whatsapp_status": {
        "openai": "1024x1536",
        "final": (1080, 1920),
        "aspect_desc": "vertical 9:16",
    },
    "whatsapp_post": {
        "openai": "1024x1024",
        "final": (1080, 1080),
        "aspect_desc": "square 1:1",
    },
}


def _target_or_default(target_format: str | None) -> str:
    if target_format in TARGET_FORMATS:
        return target_format
    return DEFAULT_TARGET if DEFAULT_TARGET in TARGET_FORMATS else "instagram_feed_quadrado"

# Fundos premium pré-aprovados (rotativos por padrão)
FUNDOS_PREMIUM = [
    "mármore branco com veios suaves cinza claro",
    "linho cru natural com textura visível",
    "mesa de madeira clara com luz natural lateral",
    "tecido toile azul e branco delicadamente dobrado",
    "fundo de paisagem matinal com luz dourada suave fora de foco",
]

# Identidade visual fixa (paleta + estilo)
BRAND_STYLE = (
    "estilo fotográfico premium boutique, iluminação natural suave, "
    "paleta neutra com acentos em azul royal e branco porcelana, "
    "composição clean e atemporal, sofisticada, sem ruído visual, "
    "qualidade editorial de revista de decoração, profundidade de campo rasa"
)

# Defaults globais do banner (Aline edita via "Avançado" quando precisa)
DEFAULTS_BANNER = {
    "top_label": "EXCLUSIVO GRUPO VIP",
    "cta_top": "Reserve a sua",
    "cta_bottom": "CHAME NO PRIVADO →",
}


def _format_brl(value) -> str:
    """Formata valor numérico ou string numérica como 'R$ 1.095,00'."""
    if value in (None, "", 0, "0"):
        return ""
    try:
        num = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return ""
    inteiro, _, dec = f"{num:.2f}".partition(".")
    # separador de milhar com ponto, decimal com vírgula
    inteiro_fmt = ""
    for i, ch in enumerate(reversed(inteiro)):
        if i and i % 3 == 0:
            inteiro_fmt = "." + inteiro_fmt
        inteiro_fmt = ch + inteiro_fmt
    return f"R$ {inteiro_fmt},{dec}"


# Restrições absolutas (anti-promocional + preservar produto real)
PRESERVE_NOTES = (
    "REGRAS RÍGIDAS DE PRESERVAÇÃO DO PRODUTO: "
    "1. Mantenha EXATAMENTE os mesmos objetos visíveis na foto fornecida, sem adicionar "
    "ou remover nenhum item. Mesmo formato, mesma cor, mesmo material, mesma proporção. "
    "2. NÃO invente produtos novos. NÃO substitua os produtos por outros similares. "
    "3. Não inclua texto, preço, selo, watermark ou logotipo. "
    "4. Se houver dúvida sobre algum detalhe, copie fielmente o que aparece na imagem fornecida."
)


# ----------------------------------------------------------------------------
# PROMPT BUILDERS
# ----------------------------------------------------------------------------

def _ctx_produto(produto: dict) -> str:
    """Resumo do produto (referência, não para invenção)."""
    partes = [f"este produto chama-se '{produto['nome']}'"]
    if produto.get("categoria"):
        partes.append(f"categoria interna: {produto['categoria']}")
    if produto.get("descricao_breve"):
        partes.append(f"contexto da loja: {produto['descricao_breve']}")
    return ". ".join(partes) + ". Os itens reais a fotografar são os que aparecem na foto fornecida (não os descritos no texto)."


def _prompt_variation(produto: dict, fundo_idx: int) -> str:
    fundo = FUNDOS_PREMIUM[fundo_idx % len(FUNDOS_PREMIUM)]
    return (
        f"Recompõe a cena da foto fornecida em uma nova ambientação com fundo de {fundo}. "
        f"Reposicione e re-ilumine os MESMOS objetos da foto (não troque por outros, "
        f"não adicione itens novos), variando apenas ângulo de câmera e luz. "
        f"{_ctx_produto(produto)} "
        f"{BRAND_STYLE}. {PRESERVE_NOTES}"
    )


def _prompt_bg_swap(produto: dict, cenario: str) -> str:
    return (
        f"Substitua APENAS o fundo da foto fornecida por: {cenario}. "
        f"Mantenha os objetos principais (produto) na mesma posição, mesma escala, "
        f"mesma iluminação relativa e mesma cor. Não adicione objetos novos ao redor. "
        f"{_ctx_produto(produto)} "
        f"{BRAND_STYLE}. {PRESERVE_NOTES}"
    )


def _prompt_card(produto: dict) -> str:
    return (
        f"A partir da foto fornecida, isole o produto principal e re-enquadre em "
        f"composição vertical centralizada, com espaço respirável ao redor sobre fundo "
        f"de {FUNDOS_PREMIUM[0]}. Use o mesmo produto que aparece na foto (não substitua "
        f"por outro objeto similar). Luz lateral suave realçando textura. "
        f"{_ctx_produto(produto)} "
        f"{BRAND_STYLE}. {PRESERVE_NOTES}"
    )


# ----------------------------------------------------------------------------
# PROMPT BUILDER — AI BANNER (modo B, principal)
# ----------------------------------------------------------------------------

def _resolved_banner_data(produto: dict, banner: dict | None) -> dict:
    """Mescla defaults globais com dados do produto e overrides do payload."""
    b = banner or {}
    return {
        "product_name": produto.get("nome") or "",
        "collection": (produto.get("colecao") or "").strip(),
        "price": _format_brl(produto.get("faixa_preco")),
        "top_label": (b.get("top_label") or DEFAULTS_BANNER["top_label"]).strip(),
        "cta_top": (b.get("cta_top") or DEFAULTS_BANNER["cta_top"]).strip(),
        "cta_bottom": (b.get("cta_bottom") or DEFAULTS_BANNER["cta_bottom"]).strip(),
        "custom_prompt": (b.get("custom_prompt") or "").strip(),
    }


def _prompt_ai_banner(produto: dict, banner_resolved: dict, target_format: str) -> str:
    """
    Prompt em inglês para o banner com texto (modo B).
    Mantém slots em português brasileiro para preservar acentos e tom.
    """
    cfg = TARGET_FORMATS[target_format]
    aspect = cfg["aspect_desc"]
    bd = banner_resolved

    # Linha de coleção é opcional — se vazio, omitir
    collection_line = (
        f"  Collection:     {bd['collection']}\n"
        if bd["collection"] else ""
    )
    custom_append = (
        f"\nINSTRUÇÃO ADICIONAL DA EQUIPE: {bd['custom_prompt']}"
        if bd["custom_prompt"] else ""
    )

    return (
        f"Create a premium {aspect} promotional banner for a luxury home decor boutique.\n"
        f"Use the uploaded product photo as the main product reference. Preserve the product faithfully.\n\n"
        f"Design style: elegant, feminine, refined, minimal, editorial, luxury boutique aesthetic. "
        f"Warm off-white background. Generous spacing. Thin decorative lines. Clean composition. "
        f"High-end home decor campaign look.\n\n"
        f"Typography: elegant high-contrast serif italic for product name and price. "
        f"Thin widely-spaced sans-serif for secondary text.\n\n"
        f"Text to render EXACTLY (preserve Portuguese accents, do not misspell, do not add or remove words):\n"
        f"  Brand:          haus.\n"
        f"  Top label:      {bd['top_label']}\n"
        f"  Product name:   {bd['product_name']}\n"
        f"{collection_line}"
        f"  Price:          {bd['price']}\n"
        f"  CTA:            {bd['cta_top']}\n"
        f"                  {bd['cta_bottom']}\n\n"
        f"Layout: brand small top center → top label with thin horizontal lines on each side → "
        f"product image centered → product name large and elegant below the image → "
        f"collection name smaller below product name → price prominent → CTA small refined at the bottom.\n\n"
        f"Rules: no watermark, no extra logo, no random text, no price tags or stickers, no Instagram-style icons. "
        f"Keep all text clean, elegant and legible. Render Portuguese accents (ã, ç, ó, é, à) correctly.\n"
        f"PRESERVE the product from the uploaded photo — do not invent a different product."
        f"{custom_append}\n\n"
        f"{BRAND_STYLE}. {PRESERVE_NOTES}"
    )


def _prompt_retry_text_fix(produto: dict, banner_resolved: dict) -> str:
    """
    Prompt para corrigir SÓ o texto de um banner que já ficou bonito visualmente.
    Esperamos que a edição rode sobre a imagem PROCESSED anterior.
    """
    bd = banner_resolved
    collection_line = (
        f"  Collection:     {bd['collection']}\n"
        if bd["collection"] else ""
    )
    return (
        f"This image is a finished luxury boutique banner. The visual composition, photo, "
        f"colors, layout, decorative lines and typography style are CORRECT — keep them.\n"
        f"Your only task: fix the TEXT content so it reads EXACTLY as below (preserve Portuguese accents, "
        f"do not misspell, do not add or remove any word):\n\n"
        f"  Brand:          haus.\n"
        f"  Top label:      {bd['top_label']}\n"
        f"  Product name:   {bd['product_name']}\n"
        f"{collection_line}"
        f"  Price:          {bd['price']}\n"
        f"  CTA:            {bd['cta_top']}\n"
        f"                  {bd['cta_bottom']}\n\n"
        f"Do NOT redesign the banner. Do NOT change the photo, colors, fonts or layout. "
        f"Re-render only the text labels above, fixing any typo or wrong character."
    )


# ----------------------------------------------------------------------------
# OPENAI CLIENT (lazy)
# ----------------------------------------------------------------------------

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY não configurada no ambiente. "
                "Defina antes de rodar o image_engine."
            )
        _client = OpenAI()
    return _client


# ----------------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------------

def _salvar_b64(b64_data: str, product_id: int, suffix: str = "png") -> tuple[Path, str]:
    """Decodifica base64 e salva em media/processed/<pid>/. Retorna (path_absoluto, path_relativo)."""
    pasta = PROCESSED_DIR / str(product_id)
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"gen_{int(time.time()*1000)}.{suffix}"
    destino = pasta / nome
    destino.write_bytes(base64.b64decode(b64_data))
    relpath = f"media/processed/{product_id}/{nome}"
    return destino, relpath


def _extrair_b64(resp) -> str:
    """Extrai b64 do response da OpenAI (compatível gpt-image-1 e DALL-E 3 b64)."""
    item = resp.data[0]
    b64 = getattr(item, "b64_json", None)
    if not b64:
        raise RuntimeError(
            "Resposta da OpenAI sem b64_json. "
            "Confirme que o modelo usado retorna b64 (gpt-image-1 retorna por padrão)."
        )
    return b64


# ----------------------------------------------------------------------------
# OPERAÇÕES (1 chamada → 1 imagem)
# ----------------------------------------------------------------------------

def _gerar_do_zero(prompt: str, openai_size: str = "1024x1024") -> str:
    """Generations: cria imagem do zero a partir do prompt."""
    client = _get_client()
    resp = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=openai_size,
        n=1,
        **({"quality": DEFAULT_QUALITY} if IMAGE_MODEL.startswith("gpt-image") else {}),
    )
    return _extrair_b64(resp)


def _editar(image_path: Path, prompt: str, openai_size: str = "1024x1024") -> str:
    """Edits: edita imagem existente com prompt."""
    client = _get_client()
    with open(image_path, "rb") as f:
        resp = client.images.edit(
            model=IMAGE_MODEL,
            image=f,
            prompt=prompt,
            size=openai_size,
            n=1,
        )
    return _extrair_b64(resp)


def _post_process(abs_path: Path, target_format: str) -> tuple[int, int]:
    """
    Ajusta o PNG gerado pela OpenAI para o tamanho final exato da rede social,
    usando crop center + resize com Pillow. Sobrescreve o próprio arquivo.

    Retorna (width, height) finais.
    """
    cfg = TARGET_FORMATS[target_format]
    final_w, final_h = cfg["final"]
    target_ratio = final_w / final_h

    from PIL import Image  # import preguiçoso (Pillow já está instalado)

    with Image.open(abs_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        ratio = w / h

        # Crop center para o aspect ratio alvo, depois resize ao tamanho final
        if abs(ratio - target_ratio) > 0.005:
            if ratio > target_ratio:
                # Imagem mais larga que o alvo → corta laterais
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                im = im.crop((left, 0, left + new_w, h))
            else:
                # Imagem mais alta que o alvo → corta topo/baixo
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                im = im.crop((0, top, w, top + new_h))

        im = im.resize((final_w, final_h), Image.LANCZOS)
        im.save(abs_path, format="PNG", optimize=True)

    return final_w, final_h


# ----------------------------------------------------------------------------
# API PRINCIPAL
# ----------------------------------------------------------------------------

def tratar_foto(
    media_id: int,
    presets: list[Preset] | None = None,
    n_por_preset: int = 1,
    target_format: str | None = None,
    custom_prompt: str | None = None,
) -> list[int]:
    """
    Gera versões processadas (modo editorial_photo) a partir de uma foto raw.

    Args:
        media_id: id da mídia raw no product_media
        presets: lista de presets a aplicar (default: ["variation"])
        n_por_preset: quantas variações por preset (default 1)
        target_format: formato alvo (instagram_feed_quadrado etc); default = DEFAULT_TARGET
        custom_prompt: texto livre adicional anexado ao prompt-base

    Returns:
        Lista de ids das novas mídias processadas registradas.
    """
    presets = presets or ["variation"]
    target_format = _target_or_default(target_format)
    cfg = TARGET_FORMATS[target_format]
    openai_size = cfg["openai"]

    media = get_product_media(media_id)
    if not media:
        raise ValueError(f"mídia {media_id} não encontrada")
    if media["kind"] != "raw":
        raise ValueError(f"mídia {media_id} não é raw, é {media['kind']}")

    produto = get_product(media["product_id"])
    if not produto:
        raise ValueError(f"produto da mídia {media_id} não encontrado")

    raw_path = ROOT / media["filepath"]
    if not raw_path.exists():
        raise FileNotFoundError(f"arquivo raw ausente: {raw_path}")

    custom_prompt_clean = (custom_prompt or "").strip()

    novos_ids: list[int] = []
    for preset in presets:
        for i in range(n_por_preset):
            if preset == "variation":
                prompt = _prompt_variation(produto, fundo_idx=i)
            elif preset == "bg_swap":
                cenario = FUNDOS_PREMIUM[(i + 1) % len(FUNDOS_PREMIUM)]
                prompt = _prompt_bg_swap(produto, cenario)
            elif preset == "card":
                prompt = _prompt_card(produto)
            else:
                raise ValueError(f"preset inválido: {preset}")

            if custom_prompt_clean:
                prompt = f"{prompt} INSTRUÇÃO ADICIONAL DA EQUIPE: {custom_prompt_clean}"

            # Todos os presets usam edit() para preservar produto real da foto
            b64 = _editar(raw_path, prompt, openai_size=openai_size)

            abs_path, relpath = _salvar_b64(b64, produto["id"])
            w, h = _post_process(abs_path, target_format)

            novo_id = add_product_media(
                product_id=produto["id"],
                kind="processed",
                filepath=relpath,
                preset=preset,
                prompt_used=prompt,
                source_media_id=media_id,
                mode="editorial_photo",
                target_format=target_format,
                width=w,
                height=h,
                banner_payload={"custom_prompt": custom_prompt_clean} if custom_prompt_clean else None,
            )
            novos_ids.append(novo_id)

    return novos_ids


# ----------------------------------------------------------------------------
# API PRINCIPAL — MODO B (AI BANNER) e MODO D (RETRY TEXT FIX)
# ----------------------------------------------------------------------------

def gerar_ai_banner(
    media_id: int,
    banner: dict | None = None,
    target_format: str | None = None,
) -> int:
    """
    Modo B: gera UM banner completo com texto via IA, a partir de uma foto raw.

    Args:
        media_id: id da foto raw
        banner: overrides opcionais (top_label, cta_top, cta_bottom, custom_prompt)
        target_format: formato alvo da rede social

    Returns:
        id da nova mídia processed registrada (mode='ai_banner').
    """
    target_format = _target_or_default(target_format)
    openai_size = TARGET_FORMATS[target_format]["openai"]

    media = get_product_media(media_id)
    if not media:
        raise ValueError(f"mídia {media_id} não encontrada")
    if media["kind"] != "raw":
        raise ValueError("AI banner precisa partir de mídia raw")

    produto = get_product(media["product_id"])
    if not produto:
        raise ValueError(f"produto da mídia {media_id} não encontrado")

    raw_path = ROOT / media["filepath"]
    if not raw_path.exists():
        raise FileNotFoundError(f"arquivo raw ausente: {raw_path}")

    banner_resolved = _resolved_banner_data(produto, banner)
    prompt = _prompt_ai_banner(produto, banner_resolved, target_format)

    b64 = _editar(raw_path, prompt, openai_size=openai_size)
    abs_path, relpath = _salvar_b64(b64, produto["id"])
    w, h = _post_process(abs_path, target_format)

    return add_product_media(
        product_id=produto["id"],
        kind="processed",
        filepath=relpath,
        preset=None,
        prompt_used=prompt,
        source_media_id=media_id,
        mode="ai_banner",
        target_format=target_format,
        width=w,
        height=h,
        banner_payload=banner_resolved,
    )


def corrigir_texto_banner(
    media_id_processed: int,
    banner: dict | None = None,
) -> int:
    """
    Modo D: re-edita um banner já gerado preservando layout/foto/cores,
    corrigindo apenas o texto.

    Args:
        media_id_processed: id de uma mídia processed (kind='processed', idealmente mode='ai_banner')
        banner: overrides do banner_data (usa o original como base se ausente)

    Returns:
        id da nova mídia processed registrada (mode='retry_fix').
    """
    media = get_product_media(media_id_processed)
    if not media:
        raise ValueError(f"mídia {media_id_processed} não encontrada")
    if media["kind"] != "processed":
        raise ValueError("retry-texto precisa de mídia processed")

    produto = get_product(media["product_id"])
    if not produto:
        raise ValueError(f"produto da mídia {media_id_processed} não encontrado")

    src_path = ROOT / media["filepath"]
    if not src_path.exists():
        raise FileNotFoundError(f"arquivo source ausente: {src_path}")

    # Reusar banner_payload anterior como base, sobrescrever com novos overrides
    payload_anterior: dict = {}
    if media.get("banner_payload"):
        import json as _json
        try:
            payload_anterior = _json.loads(media["banner_payload"]) or {}
        except (TypeError, ValueError):
            payload_anterior = {}
    merged_override = {**payload_anterior, **(banner or {})}
    banner_resolved = _resolved_banner_data(produto, merged_override)

    target_format = _target_or_default(media.get("target_format"))
    openai_size = TARGET_FORMATS[target_format]["openai"]

    prompt = _prompt_retry_text_fix(produto, banner_resolved)
    b64 = _editar(src_path, prompt, openai_size=openai_size)
    abs_path, relpath = _salvar_b64(b64, produto["id"])
    w, h = _post_process(abs_path, target_format)

    return add_product_media(
        product_id=produto["id"],
        kind="processed",
        filepath=relpath,
        preset=None,
        prompt_used=prompt,
        source_media_id=media_id_processed,
        mode="retry_fix",
        target_format=target_format,
        width=w,
        height=h,
        banner_payload=banner_resolved,
    )


# ----------------------------------------------------------------------------
# CLI básico (debug manual)
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python image_engine.py <media_id> [preset1,preset2,...] [n_por_preset]")
        print("Presets disponíveis: variation, bg_swap, card")
        sys.exit(1)
    media_id = int(sys.argv[1])
    presets = sys.argv[2].split(",") if len(sys.argv) > 2 else ["variation"]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    print(f"Tratando media_id={media_id} presets={presets} n={n} modelo={IMAGE_MODEL}")
    novos = tratar_foto(media_id, presets=presets, n_por_preset=n)
    print(f"OK Geradas {len(novos)} novas mídias: ids {novos}")
