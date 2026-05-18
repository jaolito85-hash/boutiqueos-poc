"""
fix_nome.py — Corrige Aline → Fernanda nas mensagens ja no banco

Em vez de gastar API pra re-gerar todas as mensagens, faz um simples
text replace na coluna `mensagem` do SQLite. R$ 0 de custo.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from database import get_connection


def main():
    conn = get_connection()
    try:
        # Conta antes
        before = conn.execute("""
            SELECT COUNT(*) as total FROM prospects
            WHERE mensagem LIKE '%Aline%'
        """).fetchone()
        print(f"Antes: {before['total']} mensagens com 'Aline'")

        # Substitui em batch
        cursor = conn.execute("""
            UPDATE prospects
            SET mensagem = REPLACE(mensagem, 'Aline, da haus', 'Fernanda, da haus')
            WHERE mensagem LIKE '%Aline, da haus%'
        """)
        atualizadas = cursor.rowcount

        # Tambem trocar variacoes possiveis (caso a IA tenha escrito sem virgula, etc)
        conn.execute("""
            UPDATE prospects
            SET mensagem = REPLACE(mensagem, 'Aline da haus', 'Fernanda da haus')
            WHERE mensagem LIKE '%Aline da haus%'
        """)

        conn.commit()
        print(f"Atualizadas: {atualizadas} mensagens")

        # Mostra exemplo
        sample = conn.execute("""
            SELECT username, mensagem FROM prospects
            WHERE status = 'READY' LIMIT 1
        """).fetchone()
        if sample:
            print(f"\nExemplo da mensagem corrigida (@{sample['username']}):")
            print("─" * 60)
            print(sample['mensagem'])
            print("─" * 60)

        # Confirma que sumiu
        after = conn.execute("""
            SELECT COUNT(*) as total FROM prospects
            WHERE mensagem LIKE '%Aline%'
        """).fetchone()
        print(f"\nDepois: {after['total']} mensagens ainda com 'Aline'")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
