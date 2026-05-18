"""
05_enriquecer.py — Enriquece os perfis REVIEW com dados completos

A primeira passada (02_scraper) pega só dados basicos (username, nome).
Esse script:
1. Pega os top 30 perfis status=REVIEW (ordenados por score DESC)
2. Pra cada um, chama o Apify de novo pegando perfil DETALHADO (bio + legendas)
3. Salva no banco
4. Re-qualifica com a IA usando os dados ricos

Como rodar:
    python 05_enriquecer.py [quantidade]

Default: 30 perfis. Pode passar outro numero como argumento.

Custo: ~$0.05/perfil enriquecido (Apify) + ~$0.005/perfil (IA) = ~R$ 0.30/perfil
Pra 30 perfis: ~R$ 9
"""

import os
import sys
import time
import json
from pathlib import Path

try:
    from apify_client import ApifyClient
except ImportError:
    print("ERRO: pip install apify-client")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from database import (
    get_connection,
    save_qualification,
    log_event,
)
from test_agente_openai import avaliar


APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
if not APIFY_TOKEN:
    print("ERRO: APIFY_TOKEN nao configurado")
    sys.exit(1)

LIMITE = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ACTOR_ID = "apify/instagram-scraper"


# ============================================================================
# PEGAR OS TOP N PERFIS REVIEW
# ============================================================================

def get_top_review(limit):
    """Os perfis REVIEW com maior score, ordem decrescente."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM prospects
            WHERE status = 'REVIEW'
            ORDER BY score DESC, qualified_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================================
# ENRIQUECER UM PERFIL VIA APIFY
# ============================================================================

def enriquecer_perfil(client, username):
    """
    Chama o actor do Apify pra pegar dados detalhados do perfil.
    Retorna dict com bio, posts, etc — ou None se nao conseguir.
    """
    actor_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsLimit": 5,           # 5 posts mais recentes
        "resultsType": "details",    # detalhes do perfil
        "addParentData": False,
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }

    try:
        run = client.actor(ACTOR_ID).call(run_input=actor_input, timeout_secs=180)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return None
        return items[0]
    except Exception as e:
        print(f"  ERRO ao enriquecer: {type(e).__name__}: {str(e)[:100]}")
        return None


# ============================================================================
# ATUALIZAR PERFIL NO BANCO COM DADOS RICOS
# ============================================================================

def atualizar_raw_data(username, dados_completos):
    """Atualiza raw_data, bio, etc com os dados completos do enrichment."""
    conn = get_connection()
    try:
        bio = dados_completos.get("biography", "")
        seguidores = dados_completos.get("followersCount", 0)
        seguindo = dados_completos.get("followsCount", 0)
        eh_privado = 1 if dados_completos.get("private", False) else 0

        conn.execute("""
            UPDATE prospects SET
                raw_data = ?,
                bio = ?,
                seguidores = ?,
                seguindo = ?,
                eh_privado = ?
            WHERE username = ?
        """, (
            json.dumps(dados_completos, ensure_ascii=False),
            bio, seguidores, seguindo, eh_privado,
            username
        ))
        log_event(conn, username, "ENRICHED", {})
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# CONVERTER FORMATO PRA O AGENTE
# ============================================================================

def montar_perfil_pra_agente(prospect_row, dados_ricos):
    """Pega os dados ricos do Apify e monta no formato esperado pelo agente."""
    # Pegar legendas dos posts (cada post tem `caption`)
    legendas = []
    for post in dados_ricos.get("latestPosts", [])[:5]:
        caption = post.get("caption", "")
        if caption:
            legendas.append(caption[:300])  # trunca pra economizar tokens

    return {
        "username": prospect_row["username"],
        "nome_display": dados_ricos.get("fullName", ""),
        "bio": dados_ricos.get("biography", ""),
        "seguidores": dados_ricos.get("followersCount", 0),
        "seguindo": dados_ricos.get("followsCount", 0),
        "ultimas_legendas": legendas,
        "segue": [],  # Apify não retorna isso pra perfis publicos não-amigos
        "localizacao_posts": [],  # similar
    }


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 70)
    print(f"ENRIQUECIMENTO + RE-QUALIFICAÇÃO — top {LIMITE} REVIEW")
    print("=" * 70)

    top = get_top_review(LIMITE)
    if not top:
        print("Nenhum perfil em REVIEW. Roda 02_scraper.py + 03_pipeline.py primeiro.")
        return

    print(f"\nProcessando {len(top)} perfis...\n")

    client = ApifyClient(APIFY_TOKEN)
    promovidos = 0
    custo_total = 0.0

    for i, prospect in enumerate(top, 1):
        username = prospect["username"]
        score_antigo = prospect["score"]
        print(f"[{i}/{len(top)}] @{username} (score antigo: {score_antigo})...", flush=True)

        # 1. Enriquecer via Apify
        dados_ricos = enriquecer_perfil(client, username)
        if not dados_ricos:
            print("  WARN nao conseguiu enriquecer, pulando")
            continue

        # 2. Atualizar no banco
        atualizar_raw_data(username, dados_ricos)

        # 3. Re-qualificar com IA
        try:
            perfil = montar_perfil_pra_agente(prospect, dados_ricos)
            resultado, usage = avaliar(perfil)
            save_qualification(username, resultado)

            score_novo = resultado.get("score")
            status_novo = resultado.get("status")

            # Custo OpenAI
            custo = (usage.prompt_tokens * 0.40 + usage.completion_tokens * 1.60) / 1_000_000
            custo_total += custo

            promoveu = "PROMOVIDO!" if status_novo == "APROVAR" else ""
            print(f"  -> score {score_novo} ({status_novo}) {promoveu}")
            if status_novo == "APROVAR":
                promovidos += 1

        except Exception as e:
            print(f"  ERRO na qualificacao: {type(e).__name__}: {str(e)[:100]}")

        # Pequeno delay pra nao sobrecarregar Apify
        time.sleep(1)

    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO")
    print("=" * 70)
    print(f"Perfis enriquecidos:    {len(top)}")
    print(f"Promovidos pra READY:   {promovidos}")
    print(f"Custo OpenAI:           ${custo_total:.4f} (R$ {custo_total * 5:.2f})")
    print(f"Custo Apify (estimado): ~${LIMITE * 0.05:.2f} (R$ {LIMITE * 0.05 * 5:.2f})")
    print()
    print("Proximo passo: abra o painel http://localhost:8000")
    print("Os APROVADOS agora aparecem na fila da Aline.")


if __name__ == "__main__":
    main()
