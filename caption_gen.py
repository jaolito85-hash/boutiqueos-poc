"""
caption_gen.py — Gera legendas/copy para Instagram, Story, TikTok e WhatsApp VIP
a partir de um produto cadastrado. Stack: OpenAI gpt-4.1-mini, JSON estruturado.

API principal:
    gerar_captions(product_id, banner_payload=None) -> dict[str, str]

Retorna {
    "instagram_feed": "...",
    "instagram_story": "...",
    "tiktok": "...",
    "whatsapp_grupo": "..."
}

Configuração:
    OPENAI_API_KEY — chave OpenAI (mesma do outbound)
    HAUS_CAPTION_MODEL — modelo (default: 'gpt-4.1-mini')
"""

import os
import json
from openai import OpenAI
from database import get_product

CAPTION_MODEL = os.getenv("HAUS_CAPTION_MODEL", "gpt-4.1-mini")
GRUPO_VIP_URL = "vip-haus.vercel.app"

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY não configurada no ambiente.")
        _client = OpenAI()
    return _client


SYSTEM_PROMPT = f"""Você é a Curadora de Conteúdo da haus tableware — loja-boutique premium de mesa posta, decoração e presentes em Umuarama, PR.

Sua tarefa: gerar 4 textos de marketing para 1 produto, cada um adaptado para uma plataforma diferente.

# CONTEXTO DA HAUS

A haus tableware é uma loja-boutique de produtos premium para casa, com mix variado:
- Porcelana e louças finas em vários estilos (clássicos e contemporâneos)
- Conjuntos de xícaras, jogos de chá, taças e cristais
- Le Creuset (xícaras, moedores, garrafas, panelas e demais peças)
- Eletros vintage/retrô — destaque pra linha Ariete (torradeiras, batedeiras, cafeteiras)
- Jogos de peças decorativas e itens de decoração
- Fragrâncias e perfumaria para casa (difusores, velas perfumadas, sprays de ambiente — marcas como L'Occitane, Lenvie, Dani Fernandes)
- Boxes presenteáveis montados pela loja combinando vários itens

Faixa de preço ampla: fragrâncias em promoção a partir de R$ 40, boxes presenteáveis de R$ 225 a R$ 1.095, peças premium individuais que podem ultrapassar R$ 1.500.

Localização e alcance: loja física em Umuarama-PR, atendendo toda a região Noroeste/Oeste do Paraná (Umuarama, Cianorte, Maringá, Toledo, Cascavel, Foz do Iguaçu, Paranavaí, Campo Mourão e cidades vizinhas).

Cliente típica: mulher 28-55, mora em cidade média do Paraná, classe média ou média-alta, valoriza casa bem cuidada, mesa posta, cantinhos aconchegantes. A Haus não é "ultra-luxo intimidador" — tem ponto de entrada acessível (vela R$ 40) e produtos premium no topo.

Operadora: Aline (assinatura padrão em mensagens diretas: "— Aline, da haus")

CTA padrão (todos os textos terminam com): link do grupo VIP — {GRUPO_VIP_URL}

# REGRAS RÍGIDAS (NUNCA QUEBRAR)
1. NUNCA mencione "promoção", "ofertão", "desconto", "queima" ou termos que queimem a estética premium.
2. NUNCA prometa entrega rápida, frete grátis ou nada operacional. Foco em desejo, estética, momento.
3. NUNCA use mais de 2 emojis por texto. Em mensagens íntimas (WhatsApp grupo), 1 emoji ou nenhum.
4. NUNCA use palavras genéricas de loja: "confira", "garanta o seu", "imperdível". Use linguagem específica e poética.
5. NUNCA invente fatos sobre o produto que não foram fornecidos (cor, material, estilo de uso).
6. NUNCA repita o nome do produto mais de 1 vez no mesmo texto.
7. Preserve acentos do português brasileiro corretamente.

# AS 4 SAÍDAS

Devolva 1 JSON com EXATAMENTE estas 4 chaves (sem texto fora do JSON):

1. **instagram_feed** (2-3 parágrafos curtos, 220-380 caracteres)
   - Conta uma micro-história ou descreve um cenário onde o produto vive
   - 3 a 5 hashtags premium ao final (ex: #mesaposta #porcelana #haustableware #decorpremium #vidadecasa)
   - Termina com convite ao grupo VIP + link

2. **instagram_story** (1 linha curta, 60-110 caracteres)
   - Frase impactante, sticker emoji sutil OK (1 max)
   - Inclui o link {GRUPO_VIP_URL}

3. **tiktok** (1-2 frases, 80-180 caracteres)
   - Mais punchy/visual, fala de "achadinho", "encontrei", "tendência"
   - 3-5 hashtags trending (#mesaposta #decor #achadinhos #porcelana #vidadecasa)

4. **whatsapp_grupo** (tom "amiga avisando", 2-4 linhas, 180-320 caracteres)
   - "oi gente / meninas" (minúsculo, intimista)
   - Apresenta o produto sem hard-sell
   - Termina com "— Aline, da haus" em linha separada

# FORMATO DE SAÍDA

Apenas JSON válido, sem comentários, sem markdown:

{{
  "instagram_feed": "...",
  "instagram_story": "...",
  "tiktok": "...",
  "whatsapp_grupo": "..."
}}
"""


def _resumo_produto(produto: dict, banner_payload: dict | None) -> str:
    """Monta o briefing do produto para passar ao modelo."""
    partes = [f"Nome do produto: {produto.get('nome') or 'sem nome'}"]

    if produto.get("categoria"):
        partes.append(f"Categoria: {produto['categoria'].replace('_', ' ')}")

    if produto.get("colecao"):
        partes.append(f"Coleção: {produto['colecao']}")

    if produto.get("faixa_preco"):
        try:
            preco = float(str(produto["faixa_preco"]).replace(",", "."))
            partes.append(f"Valor: R$ {preco:.2f}".replace(".", ","))
        except (ValueError, TypeError):
            partes.append(f"Valor: {produto['faixa_preco']}")

    if produto.get("descricao_breve"):
        partes.append(f"Descrição breve: {produto['descricao_breve']}")

    if banner_payload and banner_payload.get("top_label"):
        partes.append(f"Selo/destaque do banner: {banner_payload['top_label']}")

    return "\n".join(partes)


def gerar_captions(product_id: int, banner_payload: dict | None = None) -> dict:
    """
    Gera as 4 legendas (IG feed, Story, TikTok, WhatsApp grupo) para um produto.

    Args:
        product_id: id do produto no banco
        banner_payload: dados opcionais do banner gerado (para usar mesmo selo/CTA)

    Returns:
        dict com chaves instagram_feed / instagram_story / tiktok / whatsapp_grupo
    """
    produto = get_product(product_id)
    if not produto:
        raise ValueError(f"produto {product_id} não encontrado")

    briefing = _resumo_produto(produto, banner_payload)

    user_msg = (
        f"Gere as 4 legendas para este produto:\n\n{briefing}\n\n"
        f"Lembre: termine cada texto pertinente com convite ao grupo VIP "
        f"({GRUPO_VIP_URL}). Devolva apenas o JSON, sem comentários."
    )

    client = _get_client()
    resp = client.chat.completions.create(
        model=CAPTION_MODEL,
        temperature=0.7,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )

    raw = resp.choices[0].message.content or "{}"
    try:
        captions = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"resposta da OpenAI não é JSON válido: {e}\nRaw: {raw[:200]}")

    # Validar chaves esperadas
    esperadas = {"instagram_feed", "instagram_story", "tiktok", "whatsapp_grupo"}
    faltando = esperadas - set(captions.keys())
    if faltando:
        raise RuntimeError(f"resposta da OpenAI sem as chaves: {faltando}. Raw: {raw[:200]}")

    # Coagir tudo a string e limpar espaços extras
    return {k: str(captions[k]).strip() for k in esperadas}


# ----------------------------------------------------------------------------
# CLI básico (debug manual)
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python caption_gen.py <product_id>")
        sys.exit(1)
    pid = int(sys.argv[1])
    print(f"Gerando captions para produto {pid} (modelo: {CAPTION_MODEL})...")
    out = gerar_captions(pid)
    for k in ("instagram_feed", "instagram_story", "tiktok", "whatsapp_grupo"):
        print(f"\n=== {k.upper()} ===")
        print(out[k])
