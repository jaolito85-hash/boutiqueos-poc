"""
fix_capitalizar.py — Capitaliza início e nomes próprios nas mensagens

A IA antigamente foi instruída a escrever em minúsculo. Agora queremos
maiúscula no início de frases + nomes próprios. Faz isso por regex no
banco SQLite, sem gastar API.

Custo: R$ 0
"""

import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from database import get_connection


def capitalizar_mensagem(msg: str) -> str:
    """Aplica regras de capitalização:
    1. Primeira letra da mensagem maiúscula
    2. Letra após ponto/exclamação/interrogação maiúscula
    3. Primeira palavra após quebra de linha maiúscula (se começar com letra minúscula)
    """
    if not msg:
        return msg

    # 1. Primeira letra da mensagem maiúscula
    msg = msg[0].upper() + msg[1:]

    # 2. Letra após pontuação final + espaço maiúscula
    def cap_after_punct(m):
        return m.group(1) + m.group(2).upper()
    msg = re.sub(r'([.!?]\s+)([a-záàâãéèêíïóôõöúüçñ])', cap_after_punct, msg)

    # 3. Capitalizar palavra após quebra de linha (exceto se for assinatura "— ...")
    def cap_after_newline(m):
        return m.group(1) + m.group(2).upper()
    msg = re.sub(r'(\n)([a-záàâãéèêíïóôõöúüçñ])', cap_after_newline, msg)

    return msg


def capitalizar_nome_no_inicio(msg: str, nome: str) -> str:
    """Se o nome próprio aparece no início (após Oi ou no início da msg),
    garante que está capitalizado."""
    if not msg or not nome:
        return msg
    # Pega só o primeiro nome
    primeiro = nome.split()[0] if nome else ""
    if not primeiro:
        return msg
    primeiro_lower = primeiro.lower()
    primeiro_cap = primeiro.capitalize()

    # Substitui no início (com ou sem "Oi ")
    # Padrão 1: "Oi priscila," → "Oi Priscila,"
    msg = re.sub(
        rf'(\bOi\s+){re.escape(primeiro_lower)}\b',
        rf'\g<1>{primeiro_cap}',
        msg,
        flags=re.IGNORECASE
    )
    # Padrão 2: começa com nome próprio minúsculo → capitaliza
    msg = re.sub(
        rf'^{re.escape(primeiro_lower)}\b',
        primeiro_cap,
        msg,
        flags=re.IGNORECASE
    )
    return msg


def main():
    conn = get_connection()
    try:
        # Pegar todas as mensagens prontas
        rows = conn.execute("""
            SELECT username, mensagem, raw_data, bio FROM prospects
            WHERE mensagem IS NOT NULL AND mensagem != ''
        """).fetchall()

        print(f"Encontradas {len(rows)} mensagens. Corrigindo capitalização...\n")
        atualizadas = 0

        for row in rows:
            r = dict(row)
            msg_original = r["mensagem"]

            # Tentar extrair primeiro nome do raw_data (fullName)
            primeiro_nome = None
            try:
                import json
                raw = json.loads(r["raw_data"]) if r.get("raw_data") else {}
                fullName = raw.get("fullName", "")
                if fullName:
                    primeiro_nome = fullName.split()[0]
            except:
                pass

            # Aplicar correções
            msg_nova = capitalizar_mensagem(msg_original)
            if primeiro_nome:
                msg_nova = capitalizar_nome_no_inicio(msg_nova, primeiro_nome)

            if msg_nova != msg_original:
                conn.execute(
                    "UPDATE prospects SET mensagem = ? WHERE username = ?",
                    (msg_nova, r["username"])
                )
                atualizadas += 1
                print(f"@{r['username']}:")
                print(f"  ANTES: {msg_original[:80]}...")
                print(f"  DEPOIS: {msg_nova[:80]}...")
                print()

        conn.commit()
        print(f"OK Atualizadas: {atualizadas} de {len(rows)} mensagens")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
