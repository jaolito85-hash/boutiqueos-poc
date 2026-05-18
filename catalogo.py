"""
catalogo.py — Blueprint Flask para o Catálogo de produtos do haus content engine.

Rotas (registrar em 04_painel.py):
    GET    /api/catalogo/produtos                 lista produtos
    POST   /api/catalogo/produtos                 cria produto
    GET    /api/catalogo/produtos/<id>            detalhe + mídias
    PATCH  /api/catalogo/produtos/<id>            atualiza campos
    POST   /api/catalogo/produtos/<id>/upload     upload de foto raw
    DELETE /api/catalogo/media/<media_id>         remove uma mídia
    GET    /media/raw/<path>                      serve arquivos raw
    GET    /media/processed/<path>                serve arquivos processados
"""

import os
import re
import time
from pathlib import Path
from flask import Blueprint, request, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename

from database import (
    create_product,
    update_product,
    get_product,
    list_products,
    add_product_media,
    get_product_media,
    delete_product_media,
)

ROOT = Path(__file__).parent
MEDIA_DIR = ROOT / "media"
RAW_DIR = MEDIA_DIR / "raw"
PROCESSED_DIR = MEDIA_DIR / "processed"

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_FILE_BYTES = 25 * 1024 * 1024

bp_catalogo = Blueprint("catalogo", __name__)


def _ensure_dirs():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def _safe_filename(name: str) -> str:
    name = secure_filename(name) or "foto"
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in ALLOWED_EXT:
        ext = "jpg"
        stem = name
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem)[:60] or "foto"
    return f"{stem}_{int(time.time()*1000)}.{ext.lower()}"


# ----------------------------------------------------------------------------
# PRODUTOS
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/produtos", methods=["GET"])
def listar():
    incluir_inativos = request.args.get("inativos") == "1"
    return jsonify(list_products(ativo_apenas=not incluir_inativos))


@bp_catalogo.route("/api/catalogo/produtos", methods=["POST"])
def criar():
    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"ok": False, "error": "nome é obrigatório"}), 400
    pid = create_product(
        nome=nome,
        categoria=data.get("categoria"),
        faixa_preco=data.get("faixa_preco"),
        descricao_breve=data.get("descricao_breve"),
        colecao=data.get("colecao"),
        tags=data.get("tags") or [],
    )
    return jsonify({"ok": True, "id": pid, "produto": get_product(pid)})


@bp_catalogo.route("/api/catalogo/produtos/<int:pid>", methods=["GET"])
def detalhar(pid):
    produto = get_product(pid)
    if not produto:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    return jsonify(produto)


@bp_catalogo.route("/api/catalogo/produtos/<int:pid>", methods=["PATCH"])
def atualizar(pid):
    if not get_product(pid):
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404
    data = request.get_json(silent=True) or {}
    update_product(pid, **data)
    return jsonify({"ok": True, "produto": get_product(pid)})


# ----------------------------------------------------------------------------
# UPLOAD DE FOTO RAW
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/produtos/<int:pid>/upload", methods=["POST"])
def upload_foto(pid):
    produto = get_product(pid)
    if not produto:
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404

    if "foto" not in request.files:
        return jsonify({"ok": False, "error": "campo 'foto' ausente"}), 400

    f = request.files["foto"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "arquivo vazio"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({
            "ok": False,
            "error": f"extensão {ext} não permitida. Aceitos: {sorted(ALLOWED_EXT)}"
        }), 400

    _ensure_dirs()
    pasta = RAW_DIR / str(pid)
    pasta.mkdir(parents=True, exist_ok=True)
    nome_seguro = _safe_filename(f.filename)
    destino = pasta / nome_seguro
    f.save(destino)

    if destino.stat().st_size > MAX_FILE_BYTES:
        destino.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": "arquivo maior que 25MB"}), 400

    relpath = f"media/raw/{pid}/{nome_seguro}"
    media_id = add_product_media(
        product_id=pid,
        kind="raw",
        filepath=relpath,
    )
    return jsonify({
        "ok": True,
        "media_id": media_id,
        "filepath": relpath,
        "url": f"/{relpath}",
    })


# ----------------------------------------------------------------------------
# DELETE MÍDIA
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/media/<int:media_id>", methods=["DELETE"])
def remover_media(media_id):
    media = get_product_media(media_id)
    if not media:
        return jsonify({"ok": False, "error": "mídia não encontrada"}), 404
    abs_path = ROOT / media["filepath"]
    if abs_path.exists():
        try:
            abs_path.unlink()
        except OSError:
            pass
    delete_product_media(media_id)
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------
# TRATAMENTO IA (variações, bg_swap, card)
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/media/<int:media_id>/tratar", methods=["POST"])
def tratar(media_id):
    media = get_product_media(media_id)
    if not media:
        return jsonify({"ok": False, "error": "mídia não encontrada"}), 404
    if media["kind"] != "raw":
        return jsonify({"ok": False, "error": "só é possível tratar mídia raw"}), 400

    data = request.get_json(silent=True) or {}
    presets = data.get("presets") or ["variation", "bg_swap"]
    n_por_preset = int(data.get("n_por_preset", 1))
    if n_por_preset < 1 or n_por_preset > 5:
        return jsonify({"ok": False, "error": "n_por_preset deve estar entre 1 e 5"}), 400
    target_format = data.get("target_format")
    custom_prompt = data.get("custom_prompt")

    # import preguiçoso pra não exigir openai instalado se a rota não for usada
    from image_engine import tratar_foto

    try:
        novos_ids = tratar_foto(
            media_id,
            presets=presets,
            n_por_preset=n_por_preset,
            target_format=target_format,
            custom_prompt=custom_prompt,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "novos_media_ids": novos_ids,
        "novas_midias": [get_product_media(i) for i in novos_ids],
    })


# ----------------------------------------------------------------------------
# AI BANNER (modo B) — banner com texto via IA
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/media/<int:media_id>/banner-ia", methods=["POST"])
def banner_ia(media_id):
    media = get_product_media(media_id)
    if not media:
        return jsonify({"ok": False, "error": "mídia não encontrada"}), 404
    if media["kind"] != "raw":
        return jsonify({"ok": False, "error": "AI banner precisa partir de foto crua"}), 400

    data = request.get_json(silent=True) or {}
    target_format = data.get("target_format")
    banner = data.get("banner") or {}

    from image_engine import gerar_ai_banner

    try:
        novo_id = gerar_ai_banner(media_id, banner=banner, target_format=target_format)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "novo_media_id": novo_id,
        "nova_midia": get_product_media(novo_id),
    })


# ----------------------------------------------------------------------------
# RETRY TEXT FIX (modo D) — corrige texto de um banner mantendo layout
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/media/<int:media_id>/retry-texto", methods=["POST"])
def retry_texto(media_id):
    media = get_product_media(media_id)
    if not media:
        return jsonify({"ok": False, "error": "mídia não encontrada"}), 404
    if media["kind"] != "processed":
        return jsonify({"ok": False, "error": "retry-texto precisa de banner já gerado"}), 400

    data = request.get_json(silent=True) or {}
    banner = data.get("banner") or {}

    from image_engine import corrigir_texto_banner

    try:
        novo_id = corrigir_texto_banner(media_id, banner=banner)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "novo_media_id": novo_id,
        "nova_midia": get_product_media(novo_id),
    })


# ----------------------------------------------------------------------------
# CAPTIONS — gera 4 legendas (IG feed, Story, TikTok, WhatsApp grupo)
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/produtos/<int:pid>/captions", methods=["POST"])
def gerar_captions_route(pid):
    if not get_product(pid):
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404

    data = request.get_json(silent=True) or {}
    banner_payload = data.get("banner_payload")  # opcional, vindo do banner gerado

    from caption_gen import gerar_captions

    try:
        captions = gerar_captions(pid, banner_payload=banner_payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "captions": captions})


# ----------------------------------------------------------------------------
# PACKAGER — gera .zip com banners + 4 legendas + README
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/produtos/<int:pid>/pacote", methods=["POST"])
def gerar_pacote_route(pid):
    if not get_product(pid):
        return jsonify({"ok": False, "error": "produto não encontrado"}), 404

    data = request.get_json(silent=True) or {}
    media_ids = data.get("media_ids") or []
    captions = data.get("captions") or {}
    if not media_ids:
        return jsonify({"ok": False, "error": "nenhuma mídia selecionada"}), 400

    from packager import gerar_pacote

    try:
        zip_path = gerar_pacote(pid, media_ids, captions=captions)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "filename": zip_path.name,
        "download_url": f"/media/packages/{zip_path.name}",
    })


@bp_catalogo.route("/media/packages/<path:filename>")
def serve_pacote(filename):
    pasta = ROOT / "media" / "packages"
    if not (pasta / filename).is_file():
        abort(404)
    return send_from_directory(pasta, filename, as_attachment=True)


# ----------------------------------------------------------------------------
# VÍDEO REEL (HyperFrames) — gera MP4 9:16 a partir de uma foto/banner tratado
# ----------------------------------------------------------------------------

@bp_catalogo.route("/api/catalogo/media/<int:media_id>/video", methods=["POST"])
def gerar_video_route(media_id):
    media = get_product_media(media_id)
    if not media:
        return jsonify({"ok": False, "error": "mídia não encontrada"}), 404
    if media["kind"] not in ("raw", "processed"):
        return jsonify({"ok": False, "error": "kind inválido"}), 400
    if media.get("mode") == "video_reel":
        return jsonify({"ok": False, "error": "não dá pra gerar vídeo a partir de outro vídeo"}), 400

    data = request.get_json(silent=True) or {}
    quality = data.get("quality")

    from video_engine import gerar_video_showcase

    try:
        novo_id = gerar_video_showcase(media_id, quality=quality)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({
        "ok": True,
        "novo_media_id": novo_id,
        "nova_midia": get_product_media(novo_id),
    })


@bp_catalogo.route("/media/videos/<int:pid>/<path:filename>")
def serve_video(pid, filename):
    pasta = ROOT / "media" / "videos" / str(pid)
    if not (pasta / filename).is_file():
        abort(404)
    return send_from_directory(pasta, filename)


# ----------------------------------------------------------------------------
# SERVE ARQUIVOS DE MÍDIA (sem listar diretórios)
# ----------------------------------------------------------------------------

@bp_catalogo.route("/media/raw/<int:pid>/<path:filename>")
def serve_raw(pid, filename):
    pasta = RAW_DIR / str(pid)
    if not (pasta / filename).is_file():
        abort(404)
    return send_from_directory(pasta, filename)


@bp_catalogo.route("/media/processed/<int:pid>/<path:filename>")
def serve_processed(pid, filename):
    pasta = PROCESSED_DIR / str(pid)
    if not (pasta / filename).is_file():
        abort(404)
    return send_from_directory(pasta, filename)
