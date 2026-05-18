"""
02_scraper.py — Captura seguidoras das lojas-alvo via Apify

Usa o actor `apify/instagram-scraper` que custa ~$0.30 por 1000 perfis.
Cada execução: ~640 perfis (8 lojas ativas × 80 seguidoras média).

Como rodar:
    export APIFY_TOKEN="apify_api_..."
    python 02_scraper.py

Custo estimado por execução: ~$0.20 (R$ 1)
Frequência recomendada: 1x por semana
"""

import os
import json
import time
import sys
from pathlib import Path

# Apify SDK
try:
    from apify_client import ApifyClient
except ImportError:
    print("ERRO: Falta instalar o SDK do Apify.")
    print("Roda: pip install apify-client")
    sys.exit(1)

# Database local
from database import init_db, save_prospect_raw

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
if not APIFY_TOKEN:
    print("ERRO: APIFY_TOKEN não configurado.")
    print("Roda: $env:APIFY_TOKEN = 'apify_api_...'  (PowerShell)")
    print("  ou: export APIFY_TOKEN='apify_api_...'  (Linux)")
    sys.exit(1)

LOJAS_PATH = Path(__file__).parent / "lojas_alvo.json"
ACTOR_ID = "apify/instagram-scraper"  # actor oficial Apify pra Instagram

# ============================================================================
# CARREGAR LOJAS-ALVO
# ============================================================================

def carregar_lojas():
    """Lê lojas_alvo.json e retorna só as ativas."""
    with open(LOJAS_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    lojas_ativas = [l for l in config["lojas"] if l.get("ativo")]
    print(f"OK {len(lojas_ativas)} lojas ativas carregadas")
    for l in lojas_ativas:
        print(f"  - @{l['username']} (tier {l['tier']}, {l['cidade']}, max {l['max_seguidoras_por_run']})")
    return lojas_ativas


# ============================================================================
# SCRAPER
# ============================================================================

def scrape_loja(client: ApifyClient, loja: dict):
    """
    Roda o actor pra UMA loja e retorna lista de perfis de seguidoras.
    Importante: o actor da Apify lê POSTS recentes pra extrair quem comenta
    e curte — não dá pra pegar lista direta de seguidoras (Instagram bloqueia).
    Isso na prática é até MELHOR: pega gente ATIVA, que engaja, não follower fantasma.
    """
    print(f"\nProcessando @{loja['username']}...")

    actor_input = {
        # Busca por hashtag/usuario
        "directUrls": [f"https://www.instagram.com/{loja['username']}/"],
        # Pega N posts recentes da loja
        "resultsLimit": 5,
        # De cada post, extrai N comentadores
        "resultsType": "posts",
        # Inclui dados dos comentadores
        "addParentData": True,
        # Configurações de proxy (residencial pra evitar bloqueio)
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }

    try:
        run = client.actor(ACTOR_ID).call(run_input=actor_input, timeout_secs=300)
    except Exception as e:
        print(f"  ERRO ao rodar actor: {e}")
        return []

    # Coleta resultados
    perfis_encontrados = []
    seen_usernames = set()

    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        # item é um post. Dele extraímos os comentadores.
        comentarios = item.get("latestComments", [])
        for comentario in comentarios:
            owner = comentario.get("owner", {})
            username = owner.get("username")
            if username and username not in seen_usernames and username != loja["username"]:
                seen_usernames.add(username)
                perfis_encontrados.append({
                    "username": username,
                    "fullName": owner.get("fullName", ""),
                    "profilePicUrl": owner.get("profilePicUrl", ""),
                    "isPrivate": owner.get("isPrivate", False),
                    "isVerified": owner.get("isVerified", False),
                    "source_post": item.get("url", ""),
                    "source_loja": loja["username"]
                })

        # Limite por loja
        if len(perfis_encontrados) >= loja["max_seguidoras_por_run"]:
            break

    print(f"  OK {len(perfis_encontrados)} perfis encontrados")
    return perfis_encontrados


def enriquecer_perfil(client: ApifyClient, username: str):
    """
    Faz uma segunda chamada pra pegar dados completos do perfil
    (bio, contas seguidas, últimas legendas).
    SÓ rodar isso pra perfis que passaram filtro inicial pra economizar.
    """
    actor_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsLimit": 1,
        "resultsType": "details",
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"]
        }
    }

    try:
        run = client.actor(ACTOR_ID).call(run_input=actor_input, timeout_secs=120)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return items[0] if items else None
    except Exception as e:
        print(f"  WARN erro ao enriquecer @{username}: {e}")
        return None


# ============================================================================
# ORQUESTRADOR
# ============================================================================

def main():
    print("=" * 70)
    print("SCRAPER DE PROSPECTS — haus tableware")
    print("=" * 70)

    init_db()
    lojas = carregar_lojas()
    client = ApifyClient(APIFY_TOKEN)

    total_novos = 0
    total_duplicados = 0

    for loja in lojas:
        perfis = scrape_loja(client, loja)
        for p in perfis:
            try:
                save_prospect_raw(
                    username=p["username"],
                    source_loja=loja["username"],
                    cidade_loja=loja["cidade"],
                    raw_data=p
                )
                total_novos += 1
            except Exception as e:
                # Conflict (UNIQUE) já é tratado no INSERT...ON CONFLICT
                total_duplicados += 1

        # Pausa entre lojas pra não estressar a API
        time.sleep(2)

    print("\n" + "=" * 70)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 70)
    print(f"Perfis novos:        {total_novos}")
    print(f"Duplicados/update:   {total_duplicados}")
    print(f"\nProximo passo: rodar python 03_pipeline.py pra qualificar.")


if __name__ == "__main__":
    main()
