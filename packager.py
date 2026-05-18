"""
packager.py — Gera um arquivo ZIP com tudo que Aline precisa para postar 1 produto:
banners aprovados (PNG) + 4 legendas (.txt) + README.

Saída em media/packages/haus_<slug>_<timestamp>.zip

API principal:
    gerar_pacote(product_id, media_ids, captions) -> Path
        Cria o zip e devolve o path absoluto.

Rotas REST (registradas em catalogo.py):
    POST /api/catalogo/produtos/<pid>/pacote
    GET  /media/packages/<filename>  (serve o download)
"""

import os
import re
import time
import zipfile
from pathlib import Path

from database import get_product, get_product_media

ROOT = Path(__file__).parent
PACKAGES_DIR = ROOT / "media" / "packages"
GRUPO_VIP_URL = "vip-haus.vercel.app"

# Quais "modos" de mídia são banners postáveis (não incluímos fotos cruas no pacote)
BANNER_MODES = {"ai_banner", "retry_fix", "template_banner"}

CAPTION_FILES = [
    ("instagram_feed", "1_legenda_instagram_feed.txt"),
    ("instagram_story", "2_legenda_instagram_story.txt"),
    ("tiktok", "3_legenda_tiktok.txt"),
    ("whatsapp_grupo", "4_mensagem_whatsapp_grupo.txt"),
]


def _slugify(text: str, max_len: int = 40) -> str:
    """Slug seguro para nome de arquivo (sem acento/espaço/símbolo)."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")
    return (s[:max_len] or "produto").lower()


def _readme_body(produto: dict, n_imagens: int, captions_geradas: bool) -> str:
    nome = produto.get("nome") or "produto"
    colecao = produto.get("colecao") or ""
    valor = produto.get("faixa_preco") or ""
    return f"""haus tableware — pacote de postagem
{'-' * 40}

Produto: {nome}
Coleção: {colecao or '(sem coleção)'}
Valor:   R$ {valor}

Conteúdo deste pacote:
- {n_imagens} imagem(ns) PNG (1080×1080 ou 1080×1920 conforme formato)
- 4 arquivos de legenda (.txt) para cada plataforma {'(geradas por IA, edite antes de postar)' if captions_geradas else '(vazias — gere antes de postar)'}

Como postar:
1. Instagram Feed   → abra o arquivo 1_legenda_instagram_feed.txt e copie. Suba a foto no app, cole a legenda.
2. Instagram Story  → abra 2_legenda_instagram_story.txt. Foto no story + texto sobreposto.
3. TikTok           → abra 3_legenda_tiktok.txt. Suba o conteúdo, cole a legenda.
4. WhatsApp grupo   → abra 4_mensagem_whatsapp_grupo.txt. Envie no grupo VIP com a foto anexa.

Lembrete: o CTA principal é o link {GRUPO_VIP_URL}.

— gerado por haus studio
"""


def gerar_pacote(product_id: int, media_ids: list[int], captions: dict | None = None) -> Path:
    """
    Monta o .zip do produto. Espera que media_ids sejam de banners (kind=processed).
    Aceita também ids de fotos tratadas (mode=editorial_photo) — é livre escolha de Aline.

    Args:
        product_id: id do produto
        media_ids: ids das mídias a empacotar (ordem preservada)
        captions: dict opcional {instagram_feed, instagram_story, tiktok, whatsapp_grupo}

    Returns:
        Path absoluto do .zip criado em media/packages/
    """
    produto = get_product(product_id)
    if not produto:
        raise ValueError(f"produto {product_id} não encontrado")
    if not media_ids:
        raise ValueError("nenhuma mídia selecionada para o pacote")

    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    captions = captions or {}

    slug = _slugify(produto.get("nome") or f"produto_{product_id}")
    ts = int(time.time())
    zip_name = f"haus_{slug}_{ts}.zip"
    zip_path = PACKAGES_DIR / zip_name

    n_imagens = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Imagens
        for idx, mid in enumerate(media_ids, start=1):
            media = get_product_media(int(mid))
            if not media:
                continue
            src = ROOT / media["filepath"]
            if not src.exists():
                continue
            ext = src.suffix.lstrip(".") or "png"
            modo = (media.get("mode") or media.get("preset") or "midia").replace("/", "_")
            target = (media.get("target_format") or "").replace("/", "_")
            target_part = f"_{target}" if target else ""
            arc_name = f"imagens/{idx:02d}_{modo}{target_part}.{ext}"
            zf.write(src, arc_name)
            n_imagens += 1

        if not n_imagens:
            zip_path.unlink(missing_ok=True)
            raise ValueError("nenhuma mídia válida encontrada no disco — pacote não gerado")

        # 2. Captions
        captions_geradas = any((captions.get(k) or "").strip() for k, _ in CAPTION_FILES)
        for key, filename in CAPTION_FILES:
            texto = (captions.get(key) or "").strip()
            zf.writestr(filename, texto + ("\n" if texto else ""))

        # 3. README
        zf.writestr("LEIA-ME.txt", _readme_body(produto, n_imagens, captions_geradas))

    return zip_path


# ----------------------------------------------------------------------------
# CLI básico (debug manual)
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Uso: python packager.py <product_id> <media_id1,media_id2,...>")
        sys.exit(1)
    pid = int(sys.argv[1])
    ids = [int(x) for x in sys.argv[2].split(",") if x.strip()]
    path = gerar_pacote(pid, ids)
    print(f"OK pacote em {path}")
