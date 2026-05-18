"""
06_recalibrar.py — Re-qualifica perfis que ja foram enriquecidos
                   usando o prompt calibrado.

Pega TODOS os perfis com bio populada (status REVIEW ou DISCARDED)
e re-qualifica usando o prompt novo (versão 4, menos conservador).

NÃO chama Apify (já temos os dados). Só OpenAI.

Custo: ~$0.005 por perfil. Pra 30 perfis: ~R$ 0.80.

Roda:
    python 06_recalibrar.py
"""

import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_agente_openai import avaliar
from database import get_connection, save_qualification


def perfis_pra_recalibrar():
    """Pega perfis que tem bio populada (foram enriquecidos)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM prospects
            WHERE bio IS NOT NULL AND bio != ''
              AND status IN ('REVIEW', 'DISCARDED')
            ORDER BY score DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def montar_perfil(prospect):
    """Converte do formato do banco pro formato esperado pelo agente."""
    try:
        raw = json.loads(prospect["raw_data"]) if prospect.get("raw_data") else {}
    except:
        raw = {}

    # Pegar legendas dos posts
    legendas = []
    for post in raw.get("latestPosts", [])[:5]:
        caption = post.get("caption", "")
        if caption:
            legendas.append(caption[:300])

    return {
        "username": prospect["username"],
        "nome_display": raw.get("fullName", ""),
        "bio": raw.get("biography", "") or prospect.get("bio", ""),
        "seguidores": raw.get("followersCount", 0) or prospect.get("seguidores", 0),
        "seguindo": raw.get("followsCount", 0) or prospect.get("seguindo", 0),
        "ultimas_legendas": legendas,
        "segue": [],
        "localizacao_posts": [],
    }


def main():
    print("=" * 70)
    print("RE-CALIBRAGEM com prompt v4 (regras de bio ouro + profissional)")
    print("=" * 70)

    perfis = perfis_pra_recalibrar()
    if not perfis:
        print("Nenhum perfil com bio pra recalibrar.")
        print("Rode 05_enriquecer.py primeiro.")
        return

    print(f"\n{len(perfis)} perfis enriquecidos pra re-qualificar...\n")

    stats = {"APROVAR": 0, "REVISAR": 0, "DESCARTAR": 0}
    promovidos = []
    rebaixados = []
    custo_total = 0.0

    for i, prospect in enumerate(perfis, 1):
        username = prospect["username"]
        score_antigo = prospect["score"]
        status_antigo = prospect["status"]
        print(f"[{i}/{len(perfis)}] @{username} (era score {score_antigo}, {status_antigo})...", flush=True)

        try:
            perfil_fmt = montar_perfil(prospect)
            resultado, usage = avaliar(perfil_fmt)
            save_qualification(username, resultado)

            score_novo = resultado.get("score")
            status_novo = resultado.get("status")
            stats[status_novo] = stats.get(status_novo, 0) + 1

            custo = (usage.prompt_tokens * 0.40 + usage.completion_tokens * 1.60) / 1_000_000
            custo_total += custo

            indicador = ""
            if status_antigo != "APROVAR" and status_novo == "APROVAR":
                indicador = " >>> PROMOVIDO!"
                promovidos.append(username)
            elif status_antigo == "REVIEW" and status_novo == "DESCARTAR":
                indicador = " (rebaixado pra DESCARTAR)"
                rebaixados.append(username)

            print(f"  -> score {score_novo} ({status_novo}){indicador}")

        except Exception as e:
            print(f"  ERRO: {type(e).__name__}: {str(e)[:100]}")

        time.sleep(0.5)

    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO DA RE-CALIBRAGEM")
    print("=" * 70)
    print(f"APROVAR (READY):    {stats['APROVAR']}")
    print(f"REVISAR:            {stats['REVISAR']}")
    print(f"DESCARTAR:          {stats['DESCARTAR']}")
    print(f"\nPromovidos pra READY: {len(promovidos)}")
    for p in promovidos[:10]:
        print(f"  ✓ @{p}")
    print(f"\nCusto: ${custo_total:.4f} (R$ {custo_total * 5:.2f})")
    print(f"\nProximo passo: abra http://localhost:8000 — os APROVADOS estao lá.")


if __name__ == "__main__":
    main()
