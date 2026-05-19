"""
leads_import.py — Importação de leads via CSV externo

Rota Flask: POST /api/leads/import-csv
  multipart/form-data:
    csv:  arquivo .csv (utf-8 ou utf-8 com BOM, suporta mojibake latin-1→cp1252)
    mode: "preview" | "commit"  (default: preview)

Regras de descarte (na ordem):
  1. username_vazio
  2. temperatura_fria
  3. apostador_ativo
  4. texto_ingles      (heurística stopwords pt/en)
  5. duplicado_db      (mesma plataforma + username já no banco)

Status inicial: REVIEW (Aline aprova manualmente antes de virar READY).
Score do CSV (0-100) é normalizado para 0-10.
Mensagem é gerada por template baseado em `Intent`.
"""

import csv
import io
from flask import Blueprint, request, jsonify

from database import prospect_exists, save_imported_prospect
from links import cta_link


# ============================================================================
# CONSTANTES
# ============================================================================

MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_ROWS = 5000

# UTM tracking: link da DM saída de CSV vai como utm_source=dm_outbound_csv.
# Prefixo https:// preservado pra Instagram/WhatsApp renderizarem como link clicável.
GRUPO_VIP_URL = "https://" + cta_link("dm_outbound_csv")

# Templates de DM por intent. Premissa: a DM é enviada por um FUNCIONÁRIO da haus
# pra uma pessoa que comentou em vídeo/post de TERCEIROS (não em post da haus).
# Por isso a abertura se apresenta ("Nossa equipe viu...") e introduz a loja.
# O link é clicável (https://) e a assinatura @haus.tableware leva ao perfil
# pra seguir.
TEMPLATES_MENSAGEM = {
    "perguntando_preco": (
        "Oii! Nossa equipe viu que você tem bom gosto e procura peças exclusivas. "
        "A gente é boutique de mesa posta, porcelana e Le Creuset em Umuarama-PR.\n\n"
        "Entra no nosso grupo VIP — é onde solto valores, novidades e peças antes "
        "de qualquer rede: " + GRUPO_VIP_URL + "\n\n@haus.tableware"
    ),
    "buscando_atendimento": (
        "Oii! Nossa equipe viu que você tem bom gosto e procura peças exclusivas. "
        "A gente é boutique de mesa posta, porcelana e Le Creuset, com atendimento "
        "personalizado.\n\n"
        "Entra no nosso grupo VIP — onde a gente solta as novidades antes de "
        "qualquer outra rede: " + GRUPO_VIP_URL + "\n\n@haus.tableware"
    ),
    "perguntando_local": (
        "Oii! Nossa equipe viu que você tem bom gosto e procura peças exclusivas. "
        "A loja fica em Umuarama-PR e atende toda a região.\n\n"
        "Entra no nosso grupo VIP pra acompanhar peças, novidades e atendimento "
        "direto: " + GRUPO_VIP_URL + "\n\n@haus.tableware"
    ),
}
TEMPLATE_FALLBACK = TEMPLATES_MENSAGEM["buscando_atendimento"]

# Heurística de inglês: pelo menos 2 stopwords en E zero stopwords pt
STOPWORDS_PT = {
    "que", "não", "nao", "com", "para", "pra", "tem", "aqui", "tipo",
    "você", "voce", "são", "sao", "mais", "muito", "aí", "ai", "pelo",
    "pela", "uma", "umas", "uns", "isso", "essa", "esse", "como",
    "quando", "onde", "porque", "também", "tambem", "ainda", "muito",
    "sem", "ser", "tô", "to", "qual", "vou", "vai", "vamos",
}
STOPWORDS_EN = {
    "the", "this", "that", "what", "please", "price", "over", "just",
    "under", "love", "since", "have", "had", "they", "their", "with",
    "without", "would", "could", "should", "stoneware", "still",
    "fyp", "tell", "without", "telling",
}


# ============================================================================
# DECODE / MOJIBAKE FIX
# ============================================================================

def fix_mojibake(s: str) -> str:
    """Converte texto UTF-8 mal-interpretado como cp1252 de volta pra UTF-8.

    Idempotente: aplica fix só quando detecta marcador típico ('Ã' ou 'Â').
    Mantém o texto original se a conversão falhar.
    """
    if not s or ("Ã" not in s and "Â" not in s):
        return s
    try:
        return s.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Bytes inválidos: tenta com errors='replace' como melhor esforço
        return s.encode("cp1252", errors="replace").decode("utf-8", errors="replace")


def _decode_bytes(raw: bytes) -> str:
    """Decodifica os bytes do CSV. Lida com BOM utf-8."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", errors="replace")


# ============================================================================
# HEURÍSTICA DE INGLÊS
# ============================================================================

def is_english(txt: str) -> bool:
    """True se o texto parece ser inglês.

    Regra: ≥4 palavras, ≥2 stopwords en, 0 stopwords pt.
    Falsos-positivos em frases curtas são evitados pelo mínimo de 4 palavras.
    """
    if not txt:
        return False
    palavras = txt.lower().split()
    if len(palavras) < 4:
        return False
    palavras_set = set(palavras)
    n_en = len(palavras_set & STOPWORDS_EN)
    n_pt = len(palavras_set & STOPWORDS_PT)
    return n_en >= 2 and n_pt == 0


# ============================================================================
# PARSING + VALIDAÇÃO
# ============================================================================

CAMPOS_TEXTO = (
    "Nome", "Cidade", "Endereco", "Evidencia", "Texto original",
    "Post owner", "URL do Post", "URL do Perfil", "Perfil",
)


def _normalizar_linha(row: dict) -> dict:
    """Aplica fix_mojibake nos campos de texto e normaliza valores."""
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        v = (v or "").strip()
        if k in CAMPOS_TEXTO:
            v = fix_mojibake(v)
        out[k] = v
    # Normalizar campos críticos
    out["_plataforma"] = (out.get("Plataforma", "") or "instagram").lower().strip() or "instagram"
    if out["_plataforma"] not in ("instagram", "tiktok"):
        out["_plataforma"] = "instagram"
    perfil = out.get("Perfil", "").lstrip("@").strip()
    out["_username"] = perfil
    out["_temperatura"] = (out.get("Temperatura", "") or "").lower().strip()
    out["_intent"] = (out.get("Intent", "") or "").lower().strip()
    out["_texto_original"] = out.get("Texto original", "") or ""
    out["_nome"] = (out.get("Nome", "") or "").strip()
    # Score: clamp [0, 100]
    try:
        score_raw = int(out.get("Score", "0") or "0")
    except ValueError:
        score_raw = 0
    out["_score_csv"] = max(0, min(100, score_raw))
    return out


def _primeiro_nome(nome: str) -> str:
    """'Lice Camargo' -> 'lice'. Vazio -> '' (template usa 'oi' sem nome)."""
    if not nome:
        return ""
    p = nome.split()[0].strip().lower()
    # Remove caracteres não-letra do começo (números, _, etc)
    p = "".join(c for c in p if c.isalpha() or c in "-")
    return p


def _gerar_mensagem(linha: dict) -> str:
    """Gera a DM usando template do Intent.

    Os templates atuais são genéricos (sem nome) — qualquer funcionário pode
    enviar a mesma DM. Mantida a tolerância a `{nome}` em templates antigos
    para que customizações futuras com nome não quebrem.
    """
    template = TEMPLATES_MENSAGEM.get(linha["_intent"], TEMPLATE_FALLBACK)
    if "{nome}" not in template:
        return template
    nome = _primeiro_nome(linha.get("_nome", ""))
    if nome:
        return template.format(nome=nome)
    return template.replace(" {nome},", "").replace("{nome}", "")


def render_message_for_intent(intent: str) -> str:
    """API pública: gera a mensagem só com base no intent (útil pra backfill).

    Backfills usam só o intent armazenado em raw_data — não temos contexto
    de _nome/etc. Os templates atuais não dependem do nome.
    """
    intent = (intent or "").lower().strip()
    template = TEMPLATES_MENSAGEM.get(intent, TEMPLATE_FALLBACK)
    if "{nome}" in template:
        template = template.replace(" {nome},", "").replace("{nome}", "")
    return template


def _avaliar_linha(linha: dict, vistos_no_csv: set) -> str | None:
    """Retorna motivo de descarte (str) ou None se válida.

    Ordem importa: o primeiro motivo que matchar define o descarte.
    """
    if not linha["_username"]:
        return "username_vazio"
    if linha["_temperatura"] == "frio":
        return "temperatura_fria"
    if linha["_intent"] == "apostador_ativo":
        return "apostador_ativo"
    if is_english(linha["_texto_original"]):
        return "texto_ingles"
    chave = (linha["_plataforma"], linha["_username"])
    if chave in vistos_no_csv:
        return "duplicado_no_csv"
    if prospect_exists(*chave):
        return "duplicado_db"
    return None


def parse_csv(file_bytes: bytes) -> dict:
    """Lê o CSV, aplica fix de mojibake e separa válidos × descartados.

    Retorna:
        {
            "total": int,
            "validos": list[dict],         # linhas normalizadas, prontas pra inserir
            "descartados": list[dict],     # {linha_n, motivo, username, plataforma, preview}
            "descartados_por_motivo": {motivo: count},
        }
    """
    texto = _decode_bytes(file_bytes)
    reader = csv.DictReader(io.StringIO(texto))

    total = 0
    validos: list[dict] = []
    descartados: list[dict] = []
    contagem: dict[str, int] = {}
    vistos_no_csv: set[tuple[str, str]] = set()

    for i, row in enumerate(reader, start=2):  # linha 2 = primeira de dados (1 = header)
        total += 1
        if total > MAX_ROWS:
            raise ValueError(f"CSV excede o limite de {MAX_ROWS} linhas")
        linha = _normalizar_linha(row)
        motivo = _avaliar_linha(linha, vistos_no_csv)
        if motivo is None:
            vistos_no_csv.add((linha["_plataforma"], linha["_username"]))
            validos.append(linha)
        else:
            contagem[motivo] = contagem.get(motivo, 0) + 1
            descartados.append({
                "linha_n": i,
                "motivo": motivo,
                "plataforma": linha["_plataforma"],
                "username": linha["_username"] or "(vazio)",
                "preview": (linha["_texto_original"] or "")[:80],
            })

    return {
        "total": total,
        "validos": validos,
        "descartados": descartados,
        "descartados_por_motivo": contagem,
    }


# ============================================================================
# IMPORT
# ============================================================================

def import_leads(validos: list[dict]) -> dict:
    """Insere todos os válidos no banco como REVIEW. Idempotente via ON CONFLICT."""
    inseridos = 0
    duplicados = 0
    for linha in validos:
        score_10 = round(linha["_score_csv"] / 10)
        evidencia = linha.get("Evidencia", "") or linha.get("_texto_original", "")
        razoes = [r for r in [evidencia] if r]
        sinais = []
        if linha["_intent"]:
            sinais.append(f"intent_{linha['_intent']}")
        if linha["_temperatura"]:
            sinais.append(f"temp_{linha['_temperatura']}")
        urgencia = (linha.get("Urgencia", "") or "").lower().strip()
        if urgencia:
            sinais.append(f"urg_{urgencia}")

        raw_data = {
            "external_id": linha.get("ID", "") or None,
            "url_perfil": linha.get("URL do Perfil", "") or None,
            "url_post": linha.get("URL do Post", "") or None,
            "post_owner": linha.get("Post owner", "") or None,
            "intent": linha["_intent"] or None,
            "urgencia": urgencia or None,
            "temperatura": linha["_temperatura"] or None,
            "score_csv": linha["_score_csv"],
            "evidencia": evidencia or None,
            "texto_original": linha["_texto_original"] or None,
            "coletado_em": linha.get("Coletado em", "") or None,
            "imported_source": "csv",
        }

        resultado = save_imported_prospect(
            plataforma=linha["_plataforma"],
            username=linha["_username"],
            external_id=linha.get("ID", "") or None,
            source_loja=linha.get("Post owner", "") or "csv_import",
            cidade_loja=linha.get("Cidade", "") or None,
            score=score_10,
            confianca="media",
            razoes=razoes,
            mensagem=_gerar_mensagem(linha),
            sinais=sinais,
            raw_data=raw_data,
            status="REVIEW",
        )
        if resultado == "inserido":
            inseridos += 1
        else:
            duplicados += 1
    return {"inseridos": inseridos, "duplicados_ignorados": duplicados}


# ============================================================================
# BLUEPRINT FLASK
# ============================================================================

bp_leads = Blueprint("leads", __name__)


@bp_leads.route("/api/leads/import-csv", methods=["POST"])
def import_csv_route():
    if "csv" not in request.files:
        return jsonify({"ok": False, "error": "campo 'csv' ausente"}), 400

    f = request.files["csv"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "arquivo vazio"}), 400

    if not f.filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "error": "arquivo precisa ter extensão .csv"}), 400

    raw = f.read()
    if len(raw) > MAX_FILE_BYTES:
        return jsonify({
            "ok": False,
            "error": f"arquivo maior que {MAX_FILE_BYTES // (1024*1024)}MB"
        }), 413

    mode = (request.form.get("mode") or "preview").lower()
    if mode not in ("preview", "commit"):
        return jsonify({"ok": False, "error": "mode deve ser 'preview' ou 'commit'"}), 400

    try:
        resultado = parse_csv(raw)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 413
    except Exception as e:
        return jsonify({"ok": False, "error": f"erro ao parsear CSV: {e}"}), 400

    descartados_por_motivo = [
        {"motivo": m, "count": c}
        for m, c in sorted(resultado["descartados_por_motivo"].items(),
                           key=lambda kv: -kv[1])
    ]

    if mode == "preview":
        amostra = [
            {
                "plataforma": v["_plataforma"],
                "username": v["_username"],
                "nome": v.get("Nome", ""),
                "score": round(v["_score_csv"] / 10),
                "score_csv": v["_score_csv"],
                "intent": v["_intent"],
                "temperatura": v["_temperatura"],
                "evidencia": (v.get("Evidencia", "") or "")[:120],
            }
            for v in resultado["validos"][:10]
        ]
        return jsonify({
            "ok": True,
            "mode": "preview",
            "total": resultado["total"],
            "validos": len(resultado["validos"]),
            "descartados_por_motivo": descartados_por_motivo,
            "amostra_validos": amostra,
        })

    # mode == "commit"
    res = import_leads(resultado["validos"])
    return jsonify({
        "ok": True,
        "mode": "commit",
        "total": resultado["total"],
        "validos": len(resultado["validos"]),
        "inseridos": res["inseridos"],
        "duplicados_ignorados": res["duplicados_ignorados"],
        "descartados_por_motivo": descartados_por_motivo,
    })
