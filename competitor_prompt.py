"""
competitor_prompt.py — System prompt + JSON schema da análise de concorrente.
Separado pra facilitar iteração do prompt sem mexer no orquestrador.

A saída é validada via JSON Schema (response_format=json_schema).
"""

from textwrap import dedent


SYSTEM_PROMPT = dedent("""
Você é uma analista de marketing digital sênior especializada em marcas de tableware,
mesa posta e decoração premium no Brasil. Trabalha para a haus tableware — loja-boutique
em Umuarama-PR, com mix de porcelana, Le Creuset, fragrâncias para casa, eletros
vintage (Ariete) e boxes presenteáveis. Cliente típica: mulher 28-55, classe média a
média-alta, cidades médias do Paraná. CTA padrão da Haus: grupo VIP em vip-haus.vercel.app.

Sua tarefa: receber dados estruturados de um perfil concorrente no Instagram (perfil +
últimos posts com métricas) e devolver uma análise estratégica em JSON válido seguindo
o schema fornecido.

# DIRETRIZES DE ANÁLISE

1. **Seja específica, nunca genérica.** "Engagement baixo" não basta — diga o número e
   compare com o benchmark do nicho (mesa posta/decoração: 1-3% costuma ser saudável).

2. **Foque em GAPS, não em elogios.** O cliente quer saber o que a concorrente NÃO faz
   bem para a Haus explorar — não o que ela já faz bem.

3. **Padrões dos top posts.** Olhe os 3-5 posts de maior engajamento e extraia o que eles
   têm em comum (tema, formato, CTA, tom, gancho). Esse é o "playbook que funciona".

4. **Imitabilidade.** Para cada oportunidade, classifique:
   - "copiar": padrão validado que a Haus deve replicar (com adaptação de marca)
   - "adaptar": a ideia funciona mas precisa ser ajustada ao posicionamento Haus
   - "evitar": ela faz mas é arriscado/queima a marca Haus (ex: hard-sell, promoções agressivas)

5. **Parcerias.** Use `relatedProfiles` do perfil pra sugerir até 8 contas locais/de
   nicho que poderiam virar pipeline outbound ou colab. Priorize quem é de Umuarama
   ou região Noroeste/Oeste do Paraná, ou marcas adjacentes de tableware/decoração.

6. **Linguagem.** Português brasileiro impecável, com todos os acentos. Tom direto,
   sem floreios, sem jargão de "growth hacker". Como uma diretora de marketing
   sentando com a dona da loja.

7. **Não invente dados.** Se algum campo não tem informação suficiente, devolva array
   vazio ou string curta dizendo "dados insuficientes". Não chute números.

# SOBRE O CONTEXTO HAUS (para alinhar oportunidades)

- Posicionamento atual: tableware/decoração premium acessível, autoria curatorial.
- Estética: não é "ultra-luxo intimidador". Ponto de entrada R$40 (velas), topo R$1500+.
- Tom: poético-prático, sem hard-sell. Nunca usa "promoção", "queima", "imperdível".
- Canal principal: Instagram + grupo VIP no WhatsApp.
- Cidades-alvo: Umuarama, Cianorte, Maringá, Toledo, Cascavel, Foz, Paranavaí, Campo Mourão.

# REGRAS DE OURO

- Cada item de `oportunidades_haus` precisa ter racional concreto baseado nos dados
  recebidos (não genérico).
- `padroes_top_posts` tem que citar pelo menos 1 evidência específica (ex: "o post de
  09/05 sobre maternidade fez 332 likes / 93 comments — 5x a média").
- Se a concorrente não usa hashtags, isso é GAP CRÍTICO — sempre destacar.
- Se a frequência é < 1 post/dia, isso é oportunidade de share-of-voice.
""").strip()


# JSON Schema validado pela OpenAI (response_format=json_schema, strict=true)
ANALYSIS_SCHEMA = {
    "name": "competitor_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "posicionamento",
            "padroes_top_posts",
            "gaps",
            "oportunidades_haus",
            "parcerias_potenciais",
            "alertas",
            "resumo_executivo",
        ],
        "properties": {
            "resumo_executivo": {
                "type": "string",
                "description": "Parágrafo único (3-5 linhas) sintetizando o achado central e a recomendação de ação.",
            },
            "posicionamento": {
                "type": "string",
                "description": "Como a concorrente se posiciona em 1 frase curta. Ex: 'Consultora de mesa posta com autoridade local em Umuarama'.",
            },
            "padroes_top_posts": {
                "type": "array",
                "description": "3 a 5 padrões observados nos posts de maior engajamento, com evidência.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["padrao", "evidencia"],
                    "properties": {
                        "padrao": {"type": "string"},
                        "evidencia": {
                            "type": "string",
                            "description": "Cite pelo menos 1 post específico com data e métricas.",
                        },
                    },
                },
            },
            "gaps": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hashtags", "frequencia", "formatos", "outros"],
                "properties": {
                    "hashtags": {"type": "string"},
                    "frequencia": {"type": "string"},
                    "formatos": {"type": "string"},
                    "outros": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "oportunidades_haus": {
                "type": "array",
                "description": "5-8 ações concretas que a Haus pode tomar baseado nos gaps/padrões.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["acao", "imitabilidade", "esforco", "racional"],
                    "properties": {
                        "acao": {
                            "type": "string",
                            "description": "Ação específica e executável, não slogan.",
                        },
                        "imitabilidade": {
                            "type": "string",
                            "enum": ["copiar", "adaptar", "evitar"],
                        },
                        "esforco": {
                            "type": "string",
                            "enum": ["baixo", "medio", "alto"],
                        },
                        "racional": {
                            "type": "string",
                            "description": "Por que essa ação faz sentido com base nos dados.",
                        },
                    },
                },
            },
            "parcerias_potenciais": {
                "type": "array",
                "description": "Até 8 perfis de relatedProfiles que valem como pipeline outbound/colab para a Haus.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["handle", "tipo", "porque"],
                    "properties": {
                        "handle": {
                            "type": "string",
                            "description": "@username sem o '@' (ex: 'della_platters').",
                        },
                        "tipo": {
                            "type": "string",
                            "enum": [
                                "decoradora_arquiteta",
                                "loja_complementar",
                                "marca_referencia",
                                "influencer_lifestyle",
                                "outro",
                            ],
                        },
                        "porque": {"type": "string"},
                    },
                },
            },
            "alertas": {
                "type": "array",
                "description": "Sinais que merecem atenção: ameaças, autoridade estabelecida, mudança de posicionamento, etc.",
                "items": {"type": "string"},
            },
        },
    },
}


def build_user_message(handle: str, brief: dict) -> str:
    """Monta a mensagem do user com o brief estruturado pra o LLM.

    brief deve conter:
        - profile: dict do Apify profile-scraper (já com os campos relevantes)
        - posts: list de posts (já normalizada — ver competitor_intel._normalizar_posts)
        - metricas: dict com agregados pré-calculados
    """
    import json as _json

    return dedent(f"""
        Analise o concorrente @{handle} no Instagram.

        # PERFIL
        {_json.dumps(brief.get("profile", {}), ensure_ascii=False, indent=2)}

        # MÉTRICAS AGREGADAS (já calculadas)
        {_json.dumps(brief.get("metricas", {}), ensure_ascii=False, indent=2)}

        # POSTS (ordenados do mais recente para o mais antigo)
        {_json.dumps(brief.get("posts", []), ensure_ascii=False, indent=2)}

        Devolva APENAS o JSON seguindo o schema. Não inclua texto fora do JSON.
    """).strip()
