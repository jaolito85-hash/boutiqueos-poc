"""
07_concorrente.py — CLI da análise estratégica de concorrentes Instagram.

Subcomandos:
    analisar  — coleta + analisa 1 perfil (cache 7d default)
    comparar  — analisa N perfis e cruza gaps/oportunidades
    diff      — compara snapshot atual com snapshot mais antigo de N dias atrás
    listar    — lista snapshots já salvos no banco

Exemplos:
    python 07_concorrente.py analisar mayara.home
    python 07_concorrente.py analisar mayara.home --refresh --posts 50
    python 07_concorrente.py comparar mayara.home della_platters housedesignumuarama
    python 07_concorrente.py diff mayara.home --dias 30
    python 07_concorrente.py listar
    python 07_concorrente.py listar mayara.home

Custo estimado por execução de `analisar`:
    ~$0.07 sem cache (Apify profile $0.003 + 30 posts $0.05 + OpenAI $0.02)
    ~$0.02 com cache (só OpenAI)
"""

from __future__ import annotations

import argparse
import json
import sys

# Windows: garantir UTF-8 no stdout (cp1252 falha em '→', acentos no JSON, etc).
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from competitor_intel import analisar, comparar, diff, listar


# ============================================================================
# RENDERERS — formatos amigáveis no terminal
# ============================================================================

def _hr(c: str = "=", n: int = 70) -> str:
    return c * n


def render_analise(out: dict) -> None:
    a = out["analysis"]
    m = out["metricas"]
    p = out["profile"]

    print()
    print(_hr())
    print(f"ANÁLISE: @{out['handle']}  ({out['snapshot_date']})")
    print(_hr())

    print(f"\nNome:           {p.get('fullName') or '-'}")
    print(f"Seguidores:     {(p.get('followersCount') or 0):,}".replace(",", "."))
    print(f"Posts totais:   {(p.get('postsCount') or 0):,}".replace(",", "."))
    print(f"Verificado:     {p.get('verified')}")
    print(f"Business:       {p.get('businessCategoryName') or '-'}")
    print(f"Bio:            {(p.get('biography') or '').splitlines()[0][:120]}")

    print(f"\n--- MÉTRICAS ({m.get('posts_no_periodo')} posts no período) ---")
    print(f"Engagement rate:    {m.get('engagement_rate_pct')}%")
    print(f"Likes médio:        {m.get('likes_medio'):,}".replace(",", "."))
    print(f"Comments médio:     {m.get('comments_medio'):,}".replace(",", "."))
    print(f"Freq. posts/dia:    {m.get('freq_posts_dia')}")
    print(f"Mix formatos:       {m.get('mix_formatos')}")
    print(f"Uso de hashtags:    {m.get('uso_hashtags')}")
    print(f"Período coberto:    {m.get('periodo_coberto')}")

    print(f"\n--- POSICIONAMENTO ---\n{a.get('posicionamento')}")

    print(f"\n--- RESUMO EXECUTIVO ---\n{a.get('resumo_executivo')}")

    print(f"\n--- PADRÕES DOS TOP POSTS ---")
    for i, padrao in enumerate(a.get("padroes_top_posts", []), 1):
        print(f"{i}. {padrao.get('padrao')}")
        print(f"   evidência: {padrao.get('evidencia')}")

    print(f"\n--- GAPS ---")
    gaps = a.get("gaps", {})
    print(f"  Hashtags:    {gaps.get('hashtags')}")
    print(f"  Frequência:  {gaps.get('frequencia')}")
    print(f"  Formatos:    {gaps.get('formatos')}")
    for outro in gaps.get("outros", []):
        print(f"  Outro:       {outro}")

    print(f"\n--- OPORTUNIDADES HAUS ---")
    for i, op in enumerate(a.get("oportunidades_haus", []), 1):
        tag = f"[{op.get('imitabilidade')}/{op.get('esforco')}]"
        print(f"{i}. {tag:<22} {op.get('acao')}")
        print(f"   racional: {op.get('racional')}")

    print(f"\n--- PARCERIAS POTENCIAIS ---")
    for parc in a.get("parcerias_potenciais", []):
        print(f"  @{parc.get('handle'):<30} ({parc.get('tipo')}) — {parc.get('porque')}")

    print(f"\n--- ALERTAS ---")
    for alerta in a.get("alertas", []):
        print(f"  • {alerta}")

    print(f"\n{_hr('-')}")
    print(f"snapshot_id: {out['snapshot_id']}  |  custo: ${out['custo_usd']}")
    print(_hr())


def render_comparativo(out: dict) -> None:
    print()
    print(_hr())
    print(f"COMPARATIVO — {len(out['snapshots'])} concorrentes")
    print(_hr())

    print("\n--- MATRIZ DE MÉTRICAS ---")
    print(f"{'handle':<28} {'followers':>10} {'eng%':>6} {'freq/d':>7} {'mix':<30}")
    for row in out["matriz_metricas"]:
        mix_str = ",".join(f"{k[:3]}={v}" for k, v in (row["mix_formatos"] or {}).items())[:28]
        print(
            f"@{row['handle']:<27} "
            f"{(row['followers'] or 0):>10,}".replace(",", ".") +
            f" {row['engagement_rate_pct'] or 0:>6} "
            f"{row['freq_posts_dia'] or 0:>7} "
            f"{mix_str:<30}"
        )

    print("\n--- GAPS COMUNS A MÚLTIPLOS CONCORRENTES ---")
    if out["gaps_comuns"]:
        for g in out["gaps_comuns"]:
            print(f"  • {g} — vácuo de mercado para a Haus explorar")
    else:
        print("  (nenhum gap recorrente — concorrentes diversificam bem)")

    print("\n--- OPORTUNIDADES RECORRENTES (ações citadas em vários) ---")
    for op in out["oportunidades_recorrentes"]:
        print(f"  ({op['vezes']}x) {op['acao']}")

    custo_total = sum(s["custo_usd"] for s in out["snapshots"])
    print(f"\n{_hr('-')}\ncusto total: ${custo_total:.4f}")
    print(_hr())


def render_diff(out: dict) -> None:
    print()
    print(_hr())
    print(f"DIFF — @{out['handle']}")
    print(_hr())
    if "mensagem" in out:
        print(f"\n{out['mensagem']}")
        return

    a, ant, d = out["atual"], out["anterior"], out["delta"]
    print(f"\nAnterior:  {ant['snapshot_date']}  →  Atual: {a['snapshot_date']}  ({d['dias']} dias)\n")
    print(f"{'métrica':<20} {'anterior':>12} {'atual':>12} {'delta':>10}")
    print(f"{'-'*56}")
    print(f"{'followers':<20} {ant['followers'] or 0:>12} {a['followers'] or 0:>12} {d['followers']:>+10}")
    print(f"{'engagement_rate':<20} {ant['engagement_rate'] or 0:>12} {a['engagement_rate'] or 0:>12} {d['engagement_rate_pp']:>+10}")
    print(f"{'freq_posts_dia':<20} {ant['freq_posts_dia'] or 0:>12} {a['freq_posts_dia'] or 0:>12} {d['freq_posts_dia']:>+10}")
    print(f"\nPosicionamento mudou: {d['posicionamento_mudou']}")
    if d["posicionamento_mudou"]:
        print(f"  antes:  {ant['posicionamento']}")
        print(f"  agora:  {a['posicionamento']}")
    print(_hr())


def render_listagem(rows: list[dict]) -> None:
    print()
    print(_hr())
    print(f"SNAPSHOTS — {len(rows)} registros")
    print(_hr())
    print(f"{'data':<12} {'handle':<26} {'followers':>10} {'eng%':>6} {'freq':>6} {'custo':>8}")
    print("-" * 70)
    for r in rows:
        print(
            f"{r['snapshot_date']:<12} "
            f"@{r['username']:<25} "
            f"{(r['followers'] or 0):>10,}".replace(",", ".") +
            f" {r['engagement_rate'] or 0:>6} "
            f"{r['freq_posts_dia'] or 0:>6} "
            f"${r['custo_usd'] or 0:>7}"
        )
    print(_hr())


# ============================================================================
# CLI
# ============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="07_concorrente.py",
        description="Análise estratégica de concorrentes Instagram para a Haus",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_an = sub.add_parser("analisar", help="Coleta + analisa 1 perfil")
    p_an.add_argument("handle", help="@username (com ou sem '@')")
    p_an.add_argument("--posts", type=int, default=30, help="qtde de posts (default 30)")
    p_an.add_argument("--refresh", action="store_true", help="ignora cache Apify (re-paga)")
    p_an.add_argument("--modelo", type=str, default=None, help="override do modelo OpenAI")
    p_an.add_argument("--json", action="store_true", help="imprime JSON cru ao final")

    p_cmp = sub.add_parser("comparar", help="Analisa N perfis e cruza gaps")
    p_cmp.add_argument("handles", nargs="+", help="lista de @usernames")
    p_cmp.add_argument("--posts", type=int, default=30)
    p_cmp.add_argument("--refresh", action="store_true")

    p_diff = sub.add_parser("diff", help="Compara snapshot atual com anterior")
    p_diff.add_argument("handle")
    p_diff.add_argument("--dias", type=int, default=30, help="janela mínima do snapshot anterior")

    p_ls = sub.add_parser("listar", help="Lista snapshots salvos")
    p_ls.add_argument("handle", nargs="?", default=None, help="opcional: filtra por handle")

    args = parser.parse_args()

    if args.cmd == "analisar":
        out = analisar(
            args.handle,
            posts_limit=args.posts,
            force_refresh=args.refresh,
            modelo=args.modelo,
        )
        render_analise(out)
        if args.json:
            print("\n--- JSON CRU ---")
            print(json.dumps(out["analysis"], ensure_ascii=False, indent=2))

    elif args.cmd == "comparar":
        out = comparar(args.handles, posts_limit=args.posts, force_refresh=args.refresh)
        render_comparativo(out)

    elif args.cmd == "diff":
        out = diff(args.handle, dias=args.dias)
        render_diff(out)

    elif args.cmd == "listar":
        rows = listar(args.handle)
        render_listagem(rows)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido.")
        sys.exit(130)
    except RuntimeError as e:
        print(f"\nERRO: {e}")
        sys.exit(1)
