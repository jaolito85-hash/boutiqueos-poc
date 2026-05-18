"""
03_pipeline.py — Qualifica perfis pendentes usando o agente IA

Pega perfis com status=NEW no SQLite, roda o agente qualificador
(test_agente_openai.py), e salva o resultado no banco.

Como rodar:
    python 03_pipeline.py

Idealmente roda LOGO DEPOIS do scraper. Pode também ser cron diário.

Custo: ~$0.005 por perfil avaliado (gpt-4.1-mini)
"""

import os
import sys
import time
import json
from pathlib import Path

# Importa nosso agente
sys.path.insert(0, str(Path(__file__).parent))
from test_agente_openai import avaliar, MODEL

from database import (
    init_db,
    get_pending_qualifications,
    save_qualification,
    get_stats
)

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

# Quantos perfis processa por execução (batch)
# Pra primeira rodada, processa todos. Pra cron, pode limitar.
BATCH_SIZE = 100

# Delay entre chamadas pra não bater limite de rate (OpenAI permite muito)
DELAY_BETWEEN_CALLS = 0.5  # segundos


# ============================================================================
# CONVERSOR — formato Apify → formato esperado pelo agente
# ============================================================================

def converter_perfil(prospect_row):
    """
    Recebe um row do SQLite com raw_data do Apify e converte
    pro formato que o test_agente_openai.py espera.
    """
    raw = json.loads(prospect_row["raw_data"]) if prospect_row["raw_data"] else {}

    # O scraper do Apify retorna campos basicos (username, fullName, etc).
    # Pra qualificar bem, idealmente tem bio + legendas + lista de seguidos.
    # Mas mesmo com poucos dados o agente consegue tomar decisão.

    return {
        "username": prospect_row["username"],
        "nome_display": raw.get("fullName", ""),
        "bio": raw.get("biography", "") or prospect_row["bio"] or "",
        "seguidores": raw.get("followersCount", 0) or prospect_row["seguidores"] or 0,
        "seguindo": raw.get("followsCount", 0) or prospect_row["seguindo"] or 0,
        "ultimas_legendas": raw.get("latestPosts", []) or [],
        "segue": raw.get("following", []) or [],
        "localizacao_posts": [],  # Apify nao retorna isso direto
        "_meta": {
            "source_loja": prospect_row["source_loja"],
            "cidade_loja": prospect_row["cidade_loja"]
        }
    }


# ============================================================================
# PIPELINE
# ============================================================================

def main():
    print("=" * 70)
    print(f"PIPELINE DE QUALIFICAÇÃO — modelo {MODEL}")
    print("=" * 70)

    init_db()

    pendentes = get_pending_qualifications(limit=BATCH_SIZE)
    if not pendentes:
        print("\nNada pra qualificar. Rode o scraper primeiro:")
        print("  python 02_scraper.py")
        return

    print(f"\nProcessando {len(pendentes)} perfis pendentes...\n")

    stats_run = {"APROVAR": 0, "REVISAR": 0, "DESCARTAR": 0, "ERRO": 0}
    custo_total = 0.0

    for i, prospect in enumerate(pendentes, 1):
        username = prospect["username"]
        print(f"[{i}/{len(pendentes)}] @{username}...", end=" ", flush=True)

        try:
            perfil_formatado = converter_perfil(prospect)
            resultado, usage = avaliar(perfil_formatado)

            save_qualification(username, resultado)

            # Estatística
            stats_run[resultado.get("status", "ERRO")] += 1
            custo = (usage.prompt_tokens * 0.40 + usage.completion_tokens * 1.60) / 1_000_000
            custo_total += custo

            print(f"score {resultado['score']} ({resultado['status']})")

        except Exception as e:
            print(f"ERRO: {type(e).__name__}")
            stats_run["ERRO"] += 1

        # Delay anti-rate-limit
        time.sleep(DELAY_BETWEEN_CALLS)

    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 70)
    print(f"Aprovados (prontos):  {stats_run['APROVAR']}")
    print(f"Revisar (Aline avalia): {stats_run['REVISAR']}")
    print(f"Descartados:          {stats_run['DESCARTAR']}")
    if stats_run["ERRO"]:
        print(f"Erros:                {stats_run['ERRO']}")
    print(f"\nCusto desta execução: ${custo_total:.4f} (R$ {custo_total * 5:.2f})")

    print("\n--- Estatísticas globais do banco ---")
    stats = get_stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\nProximo passo: abrir o painel.")
    print("  cd painel/ && python -m http.server 8000")
    print("  E abrir http://localhost:8000 no navegador")


if __name__ == "__main__":
    main()
