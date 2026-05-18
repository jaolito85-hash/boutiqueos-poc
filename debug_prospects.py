"""
debug_prospects.py — diagnostico de qualidade

Mostra 5 perfis aleatorios do banco com TODOS os dados:
- O que o Apify retornou (raw_data)
- O que o agente decidiu (score, razoes, mensagem)
- Conta seguidores, posts, etc

Roda:
    python debug_prospects.py
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from database import get_connection

def main():
    conn = get_connection()
    try:
        # Pegar 5 perfis (mix: top score + alguns aleatorios)
        rows = conn.execute("""
            SELECT * FROM prospects
            WHERE status IN ('REVIEW', 'DISCARDED')
            ORDER BY score DESC
            LIMIT 5
        """).fetchall()

        if not rows:
            print("Nenhum prospect encontrado.")
            return

        print("=" * 80)
        print(f"DIAGNOSTICO — {len(rows)} perfis")
        print("=" * 80)

        for i, row in enumerate(rows, 1):
            r = dict(row)
            print(f"\n{'─' * 80}")
            print(f"[{i}] @{r['username']}  ·  score={r['score']}  ·  status={r['status']}")
            print(f"{'─' * 80}")

            print(f"\nNome:       {r.get('bio') or '(sem bio)'}")
            print(f"Seguidores: {r.get('seguidores', 0)}")
            print(f"Seguindo:   {r.get('seguindo', 0)}")
            print(f"Privado:    {r.get('eh_privado', 0)}")
            print(f"Source:     @{r.get('source_loja')} ({r.get('cidade_loja')})")

            # Razões da IA
            razoes = json.loads(r['razoes']) if r.get('razoes') else []
            if razoes:
                print(f"\nRAZÕES DO AGENTE:")
                for rz in razoes:
                    print(f"  • {rz}")

            # Dados brutos
            try:
                raw = json.loads(r['raw_data']) if r.get('raw_data') else {}
            except:
                raw = {}

            if raw:
                # Mostra campos chaves
                print(f"\nDADOS DO APIFY:")
                interessantes = ['fullName', 'biography', 'followersCount', 'followsCount',
                                'businessCategoryName', 'category', 'private', 'verified',
                                'externalUrl']
                for k in interessantes:
                    if k in raw and raw[k]:
                        val = str(raw[k])[:200]
                        print(f"  {k}: {val}")

                # Posts (legendas)
                posts = raw.get('latestPosts', [])
                if posts:
                    print(f"\nULTIMOS POSTS ({len(posts)}):")
                    for j, post in enumerate(posts[:3], 1):
                        caption = post.get('caption', '') or '(sem legenda)'
                        print(f"  {j}. {caption[:200]}")
                else:
                    print(f"\n(SEM POSTS retornados pelo Apify)")

        print(f"\n{'=' * 80}")
        # Estatisticas gerais
        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN bio IS NOT NULL AND bio != '' THEN 1 ELSE 0 END) as com_bio,
                SUM(CASE WHEN seguidores > 0 THEN 1 ELSE 0 END) as com_followers,
                SUM(CASE WHEN eh_privado = 1 THEN 1 ELSE 0 END) as privados
            FROM prospects
        """).fetchone()
        s = dict(stats)
        print(f"DIAGNOSTICO GERAL (todos os perfis no banco):")
        print(f"  Total: {s['total']}")
        print(f"  Com bio:        {s['com_bio']} ({100*s['com_bio']//max(s['total'],1)}%)")
        print(f"  Com seguidores: {s['com_followers']} ({100*s['com_followers']//max(s['total'],1)}%)")
        print(f"  Privados:       {s['privados']}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
