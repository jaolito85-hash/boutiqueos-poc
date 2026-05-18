"""
video_engine.py — Orquestra a renderização de reels 9:16 via HyperFrames.

Pipeline por job:
    1. Cria dir temporário em media/_video_jobs/<job_id>/
    2. Copia template templates/hyperframes/showcase_t1/* para o dir
    3. Copia a foto-base do produto para <dir>/assets/produto.jpg
    4. Substitui tokens ({{PRODUCT_NAME}}, {{COLLECTION_LABEL}}, {{PRICE}}, {{CTA_URL}}) nas compositions
    5. Roda `npx hyperframes render --output <abs>.mp4 --quality standard`
    6. Move MP4 para media/videos/<pid>/<timestamp>.mp4
    7. Cleanup do dir temporário
    8. Registra como product_media (kind='processed', mode='video_reel',
       target_format='instagram_story')

API principal:
    gerar_video_showcase(media_id_source, quality='standard') -> int
        Retorna o id da nova mídia (vídeo) registrada.

Configuração via env:
    HAUS_VIDEO_QUALITY        — draft / standard / high (default: standard)
    HAUS_VIDEO_TIMEOUT_SEC    — timeout do render em segundos (default: 600)
"""

import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path

from database import get_product, get_product_media, add_product_media, list_product_media
from image_engine import _format_brl  # reuso do formatador R$ 1.095,00

# Modos cujo PNG já contém texto sobreposto pela IA — ruins como fonte de vídeo
# (o template já adiciona texto próprio, daí o vídeo ficaria com texto duplicado).
BANNER_MODES = {"ai_banner", "retry_fix", "template_banner"}

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "templates" / "hyperframes" / "showcase_t1"
JOBS_DIR = ROOT / "media" / "_video_jobs"
VIDEOS_DIR = ROOT / "media" / "videos"

DEFAULT_QUALITY = os.getenv("HAUS_VIDEO_QUALITY", "standard")
RENDER_TIMEOUT = int(os.getenv("HAUS_VIDEO_TIMEOUT_SEC", "600"))
CTA_URL = "vip-haus.vercel.app"
TARGET_FORMAT_REEL = "instagram_story"  # 1080x1920 (reels também usam essa proporção)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text or "")[:40] or "produto"


def _resolve_collection_label(produto: dict) -> str:
    """Coleção em uppercase ou fallback editorial."""
    col = (produto.get("colecao") or "").strip()
    if col:
        return col.upper()
    # fallback brand-aligned (não revela ausência)
    return "EDIÇÃO PRIVADA"


def _substituir_tokens(arquivo: Path, tokens: dict[str, str]):
    """Substitui {{TOKEN}} -> valor em um arquivo. Idempotente. Falha silenciosa se token não existe."""
    txt = arquivo.read_text(encoding="utf-8")
    for k, v in tokens.items():
        txt = txt.replace(f"{{{{{k}}}}}", v)
    arquivo.write_text(txt, encoding="utf-8")


def _aplicar_template(job_dir: Path, produto: dict, foto_src: Path):
    """Copia template, copia foto-base, aplica tokens nas compositions."""
    shutil.copytree(TEMPLATE_DIR, job_dir, dirs_exist_ok=False)

    # Substituir foto placeholder pela real
    destino_foto = job_dir / "assets" / "produto.jpg"
    destino_foto.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(foto_src, destino_foto)

    # Tokens
    tokens = {
        "PRODUCT_NAME": (produto.get("nome") or "").strip(),
        "COLLECTION_LABEL": _resolve_collection_label(produto),
        "PRICE": _format_brl(produto.get("faixa_preco")) or "—",
        "CTA_URL": CTA_URL,
    }

    for f in (job_dir / "compositions").iterdir():
        if f.suffix == ".html":
            _substituir_tokens(f, tokens)


def _renderizar(job_dir: Path, output_mp4: Path, quality: str) -> dict:
    """Roda `npx hyperframes render` no job_dir. Retorna dict com stdout/stderr/returncode."""
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "npx", "--yes", "hyperframes", "render",
        "--quality", quality,
        "--output", str(output_mp4.resolve()),
    ]
    # No Windows o `npx` é encontrado via shell PATH — `shell=True` resolve o .cmd
    proc = subprocess.run(
        cmd,
        cwd=str(job_dir),
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT,
        shell=True,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def gerar_video_showcase(media_id_source: int, quality: str | None = None) -> int:
    """
    Gera reel 9:16 a partir de uma foto/banner já tratado.

    Args:
        media_id_source: id de uma product_media kind='processed'
                         (banner ou editorial photo já gerados)
        quality: 'draft' / 'standard' / 'high' (default: env HAUS_VIDEO_QUALITY)

    Returns:
        id da nova product_media (kind='processed', mode='video_reel') registrada.
    """
    quality = quality or DEFAULT_QUALITY
    if quality not in ("draft", "standard", "high"):
        raise ValueError(f"qualidade inválida: {quality}")

    media = get_product_media(media_id_source)
    if not media:
        raise ValueError(f"mídia {media_id_source} não encontrada")
    if media["kind"] not in ("processed", "raw"):
        raise ValueError(f"kind inválido: {media['kind']}")

    produto = get_product(media["product_id"])
    if not produto:
        raise ValueError(f"produto da mídia {media_id_source} não encontrado")

    # Se a fonte é um banner com texto IA, o template duplicaria texto.
    # Resolver para a foto "limpa" mais apropriada do mesmo produto:
    #   1. preferir editorial_photo mais recente
    #   2. cair para mídia raw mais recente
    foto_media = media
    if media.get("mode") in BANNER_MODES:
        candidatos = list_product_media(produto["id"])
        editorial = [m for m in candidatos if m.get("mode") == "editorial_photo"]
        raws = [m for m in candidatos if m.get("kind") == "raw"]
        if editorial:
            foto_media = editorial[-1]
        elif raws:
            foto_media = raws[-1]
        else:
            raise ValueError(
                "Produto só tem banners gerados (sem foto limpa). "
                "Gere uma 'versão sem texto' (variação ou trocar fundo) primeiro, "
                "ou suba uma foto crua."
            )

    foto_src = ROOT / foto_media["filepath"]
    if not foto_src.exists():
        raise FileNotFoundError(f"foto-base ausente: {foto_src}")

    pid = produto["id"]
    job_id = f"job_{pid}_{int(time.time() * 1000)}"
    job_dir = JOBS_DIR / job_id
    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    output_dir = VIDEOS_DIR / str(pid)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"reel_{int(time.time() * 1000)}.mp4"
    output_path = output_dir / output_filename
    relpath = f"media/videos/{pid}/{output_filename}"

    try:
        _aplicar_template(job_dir, produto, foto_src)
        result = _renderizar(job_dir, output_path, quality)
        if result["returncode"] != 0 or not output_path.exists():
            tail = (result["stderr"] or result["stdout"] or "")[-800:]
            raise RuntimeError(f"render falhou (rc={result['returncode']}): {tail}")
    finally:
        # Cleanup do job dir (mantém o MP4 já movido)
        shutil.rmtree(job_dir, ignore_errors=True)

    # Registrar no banco
    return add_product_media(
        product_id=pid,
        kind="processed",
        filepath=relpath,
        preset=None,
        prompt_used=None,
        source_media_id=media_id_source,
        mode="video_reel",
        target_format=TARGET_FORMAT_REEL,
        width=1080,
        height=1920,
        banner_payload={
            "template": "showcase_t1",
            "quality": quality,
            "duration_sec": 15,
        },
    )


# ----------------------------------------------------------------------------
# CLI básico (debug manual)
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python video_engine.py <media_id_processed> [quality=draft|standard|high]")
        sys.exit(1)
    mid = int(sys.argv[1])
    q = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"Gerando reel para media_id={mid} quality={q or DEFAULT_QUALITY}...")
    novo_id = gerar_video_showcase(mid, quality=q)
    print(f"OK novo media_id={novo_id}")
