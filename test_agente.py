"""
Teste do agente qualificador haus.
Roda 5 casos de teste cobrindo diferentes cenários:
1. APROVAR clássico (Maringá, mesa posta)
2. APROVAR Curitiba (aspiracional)
3. REVISAR (perfil cinza)
4. DESCARTAR (conta comercial)
5. CASO LIMITE (mãe de jovem em SP — fora do raio)
"""

import os
import json
import time
from anthropic import Anthropic

client = Anthropic()
MODEL = "claude-haiku-4-5"

# ============================================================================
# SYSTEM PROMPT (versão executável)
# ============================================================================

SYSTEM_PROMPT = """Você é o Agente Curador da haus tableware — uma loja premium de mesa posta, decoração e presentes em Maringá, Paraná. Sua missão é avaliar perfis do Instagram e decidir se são clientes potenciais da haus, e quando forem, escrever uma mensagem de abertura natural e específica.

# CONTEXTO DA HAUS

A haus tableware é uma loja-boutique de:
- Conjuntos de chá e porcelana (estilo toile, azul e branco, vermelho e branco)
- Boxes presenteáveis com perfumaria premium (L'Occitane, Lenvie, Dani Fernandes)
- Peças Le Creuset (xícaras, moedores, garrafas)
- Caixas decorativas estampadas exclusivas haus
- Orquídeas e kalanchoes da semana
- Faixa de preço: R$ 225 a R$ 1.095 por box

Cliente típica:
- Mulher, 28 a 55 anos
- Mora em Maringá, Curitiba, Cascavel (entrega regional)
- Renda média-alta (consome marcas como Le Creuset, L'Occitane, PatBO)
- Recebe pessoas em casa, valoriza mesa posta
- Compra presente pensado (não chocolate genérico)
- Estética: clean, atemporal, sofisticada, com toques de natureza
- Provavelmente: mãe, esposa, anfitriã, gosta de chá da tarde

# SUA TAREFA

Você recebe os dados de UM perfil do Instagram (bio, últimas legendas, contas que ela segue) e devolve um JSON com:
1. score (0 a 10)
2. status (APROVAR / REVISAR / DESCARTAR)
3. razoes (lista de 2 a 4 razões objetivas)
4. mensagem (DM personalizada de 2 a 3 linhas, em português brasileiro casual)
5. sinais (lista de tags pra segmentação futura)
6. confianca (LOW / MEDIUM / HIGH — quão certa você está do score)

# COMO PONTUAR (score 0-10)

Critérios POSITIVOS (somam ao score):
- Localização confirmada em Maringá/Curitiba/Cascavel ou região (+2)
- Posta sobre casa, decoração, mesa posta, receber pessoas (+2)
- Segue contas como @decorartemaringa, @griffdacasa, @amandalimainteriores, arquitetas, designers de interiores, lojas premium de casa (+1)
- Bio sugere mulher 28-55 (perfil profissional, casada, mãe) (+1)
- Fotos mostram casa/ambiente cuidado, mesa posta, recebendo (+2)
- Marca presença em eventos, jantares, café da manhã, brunchs (+1)
- Posta sobre filhos/família (perfil de presenteadora) (+1)

Critérios NEGATIVOS (subtraem do score):
- Conta de venda/comércio (não é cliente) (-5)
- Perfil aparentemente menor de idade (-10, vira DESCARTAR)
- Bio em outro idioma sem indicação de Brasil (-3)
- Conta privada sem dados visíveis (-2, vira REVISAR)
- Aparenta morar em cidade fora do raio de entrega (-2)
- Conta inativa há mais de 3 meses (-2)
- Conta profissional de criadora de conteúdo grande (>50k) com perfil diferente do alvo (-2 — provável não cliente, mais mídia)

# REGRAS DE STATUS

- score >= 7: APROVAR (manda DM)
- score 5-6: REVISAR (Aline decide manualmente)
- score < 5: DESCARTAR (não enviar)

# REGRAS DA MENSAGEM (CRÍTICAS)

Quando aprovado, escreva uma mensagem que SIGA TODAS estas regras:

1. ABERTURA específica e VERDADEIRA — Cite UM detalhe real do perfil (algo que você viu). Pode ser: estilo da decoração, hobby visível, post recente, cidade. NUNCA invente. Se não tem detalhe claro, use status REVISAR.

2. TOM casual, primeiro nome — "oi [nome]" (minúsculo, sem ponto de exclamação exagerado). Não usa "Olá Senhora" ou "Prezada". Sem "tudo bem?" no início (genérico demais).

3. CONEXÃO em 1 frase — "tinha tudo a ver com a haus" / "lembrei daqui" / "achei que ia gostar". Conecta o detalhe dela com o universo da haus.

4. CONVITE explícito mas LEVE — Menciona o grupo VIP de WhatsApp. Coloca o link: vip-haus.vercel.app. Termina com algo como "se quiser dar uma olhada" — sem pressão.

5. NUNCA NUNCA NUNCA:
   - Mencione preço, oferta, desconto
   - Use 3+ emojis (1 ou 2 é OK, e só se a cliente usa emoji)
   - Fale de "promoção" ou "ofertão" (queima o premium)
   - Diga "vou te adicionar" (assediador)
   - Use "amei", "adoreeei" (parece fake)
   - Mensagem ultrapassar 4 linhas

6. ASSINATURA — Sempre termina com "— Aline, da haus" em linha separada

# COMPRIMENTO E TOM

- Total: 250-400 caracteres
- 2 a 4 linhas
- Português brasileiro, descontraído mas não baixo
- Como uma amiga educada faria, não vendedora

# SINAIS PRA SEGMENTAÇÃO

Detecte e marque tags úteis pra futuro:
- mae (se aparece filhos)
- recebe_casa (mesa posta, jantares)
- esteta (cuida da estética da casa)
- presenteadora (posta presentes que dá ou recebe)
- aspiracional (consome marcas premium visíveis)
- noiva_proxima (se aparece casamento próximo)
- localizacao_maringa / localizacao_curitiba / localizacao_cascavel / localizacao_outras

# CONFIANÇA

- HIGH: bio clara, fotos com contexto, várias evidências
- MEDIUM: alguns sinais mas dados parciais
- LOW: perfil cinza, pouca informação

# FORMATO DE SAÍDA

Devolva APENAS um JSON válido, sem texto antes ou depois, neste schema exato:

{"score": 8, "status": "APROVAR", "razoes": ["..."], "mensagem": "...", "sinais": ["..."], "confianca": "HIGH"}

Importante:
- O JSON DEVE ser parseável (escape quebras de linha como \\n)
- Sem markdown, sem ```json, só o objeto
- Se confianca for LOW, prefira REVISAR sobre APROVAR
- Se status for REVISAR ou DESCARTAR, mensagem deve ser null
"""

# ============================================================================
# FEW-SHOT EXAMPLES (incluídos como histórico de mensagens)
# ============================================================================

FEW_SHOTS = [
    {
        "input": {
            "username": "vanessamoraescosta",
            "nome_display": "Vanessa Moraes",
            "bio": "Maringá-PR · Esposa do @rafamoraescosta · Mãe da Helena 🌸 · Reforma da casa nova",
            "seguidores": 1240,
            "seguindo": 893,
            "ultimas_legendas": [
                "Café da manhã de domingo com a Helena, esse lugarzinho do canto da cozinha virou meu favorito ☕️",
                "Comecei a coleção de xícaras da @decorartemaringa, são lindas demais",
                "Renovando o paisagismo do quintal antes do verão"
            ],
            "segue": ["decorartemaringa", "amandalimainteriores", "casaflormaringa", "lojahaustableware"],
            "localizacao_posts": ["Maringá, PR"]
        },
        "output": {
            "score": 9,
            "status": "APROVAR",
            "razoes": [
                "Confirmada em Maringá",
                "Posta café da manhã e cantinhos da casa",
                "Já coleciona xícaras (alta afinidade)",
                "Segue lojas-alvo da haus"
            ],
            "mensagem": "oi vanessa, vi seu canto da cozinha e fiquei encantada — esse café da manhã de domingo é tudo. lembrei daqui da haus, a gente solta novidades primeiro no grupo VIP. se quiser dar uma olhada: vip-haus.vercel.app\n\n— Aline, da haus",
            "sinais": ["localizacao_maringa", "mae", "recebe_casa", "esteta", "presenteadora"],
            "confianca": "HIGH"
        }
    },
    {
        "input": {
            "username": "lojarafadecor",
            "nome_display": "Rafa Decor Atacado",
            "bio": "🛒 Atacado pra revenda · Boleto pra primeira compra · São Paulo-SP",
            "seguidores": 8400,
            "seguindo": 23,
            "ultimas_legendas": [
                "Vasos chegando! Pedido mínimo R$ 500",
                "Promoção da semana: 30% off em luminárias",
                "Frete grátis SP"
            ],
            "segue": [],
            "localizacao_posts": ["São Paulo, SP"]
        },
        "output": {
            "score": 0,
            "status": "DESCARTAR",
            "razoes": [
                "Conta de revenda/atacado, não é consumidor final",
                "Fora do raio de entrega (SP)",
                "Perfil concorrente, não cliente"
            ],
            "mensagem": None,
            "sinais": [],
            "confianca": "HIGH"
        }
    }
]


def build_messages(perfil_real):
    """Monta o histórico de mensagens com few-shots + perfil real."""
    msgs = []
    for shot in FEW_SHOTS:
        msgs.append({"role": "user", "content": json.dumps(shot["input"], ensure_ascii=False)})
        msgs.append({"role": "assistant", "content": json.dumps(shot["output"], ensure_ascii=False)})
    msgs.append({"role": "user", "content": json.dumps(perfil_real, ensure_ascii=False)})
    return msgs


def avaliar(perfil):
    """Chama Claude e devolve o JSON parseado."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        temperature=0.6,
        system=SYSTEM_PROMPT,
        messages=build_messages(perfil)
    )
    text = response.content[0].text.strip()
    # Limpar caso o modelo coloque markdown
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text), response.usage


# ============================================================================
# CASOS DE TESTE
# ============================================================================

CASOS = [
    {
        "nome": "Caso 1 — Maringá clássica (esperado: APROVAR alto)",
        "perfil": {
            "username": "carol.coracci",
            "nome_display": "Carol Coracci",
            "bio": "Maringá · Arquiteta · Mãe da Manu e do Bento · Vou casar de novo (com o mesmo) ✨",
            "seguidores": 3400,
            "seguindo": 1850,
            "ultimas_legendas": [
                "A mesa de domingo das meninas, montei tudo no estilo provençal que eu amo",
                "Almoço de família com louças da minha avó, tradição que continuo",
                "Visita à @decorartemaringa, descobri umas coisas lindas pra casa nova",
                "O Bento começou na escolinha essa semana, choro mais que ele"
            ],
            "segue": ["decorartemaringa", "lacasapresentes", "lojahaustableware", "amandalimainteriores", "casaflormaringa", "patbo_oficial"],
            "localizacao_posts": ["Maringá, PR"]
        }
    },
    {
        "nome": "Caso 2 — Curitiba aspiracional (esperado: APROVAR alto)",
        "perfil": {
            "username": "isamendesarquitetura",
            "nome_display": "Isabela Mendes",
            "bio": "Arquiteta · Curitiba/PR · Projetos residenciais · @ateliemendes",
            "seguidores": 12300,
            "seguindo": 980,
            "ultimas_legendas": [
                "Entreguei o projeto da Mariana ontem, ficou um sonho",
                "Recebendo amigas pra um café em casa hoje, mesa toda no toile azul",
                "L'Occitane lançou a coleção nova de inverno, já é minha",
                "Curitiba amanheceu fria de novo, dia perfeito pra um chá"
            ],
            "segue": ["amandalimainteriores", "griffdacasa", "loccitaneaubresilbr", "tullistudio"],
            "localizacao_posts": ["Curitiba, PR", "Curitiba, PR"]
        }
    },
    {
        "nome": "Caso 3 — Perfil cinza (esperado: REVISAR)",
        "perfil": {
            "username": "_jessica.r",
            "nome_display": "Jé",
            "bio": "Deus em primeiro lugar 🙏",
            "seguidores": 567,
            "seguindo": 2100,
            "ultimas_legendas": [
                "Bom dia ✨",
                "Sextou 🍷",
                "Aniversário de casamento 5 anos 💍"
            ],
            "segue": ["griffdacasa", "casaflormaringa"],
            "localizacao_posts": []
        }
    },
    {
        "nome": "Caso 4 — Conta comercial (esperado: DESCARTAR)",
        "perfil": {
            "username": "decorcasa.atacado",
            "nome_display": "Decor Casa Atacado",
            "bio": "🏪 Atacadão de decoração · Pedido mínimo R$ 200 · Cascavel-PR",
            "seguidores": 4200,
            "seguindo": 89,
            "ultimas_legendas": [
                "Promoção: vasos a partir de R$ 19,90",
                "Atacado direto da fábrica, melhor preço da região",
                "Frete pra todo o sul"
            ],
            "segue": [],
            "localizacao_posts": ["Cascavel, PR"]
        }
    },
    {
        "nome": "Caso 5 — Caso limite: SP fora do raio (esperado: REVISAR ou DESCARTAR)",
        "perfil": {
            "username": "fernanda.aragao",
            "nome_display": "Fernanda Aragão",
            "bio": "São Paulo · Mãe da Helô · Comunicação · Apaixonada por mesa posta",
            "seguidores": 8900,
            "seguindo": 1200,
            "ultimas_legendas": [
                "Brunch de domingo, montei a mesa toda em azul cobalto",
                "L'Occitane novo lançamento já está em casa",
                "Helô fez 3 aninhos, dia mais especial",
                "Quem mais sofre com saudade do interior?"
            ],
            "segue": ["griffdacasa", "amandalimainteriores", "decorartemaringa", "loccitaneaubresilbr"],
            "localizacao_posts": ["São Paulo, SP"]
        }
    }
]


# ============================================================================
# EXECUÇÃO
# ============================================================================

print("=" * 80)
print("TESTE DO AGENTE QUALIFICADOR — haus tableware")
print("=" * 80)
print()

total_cost = 0
for i, caso in enumerate(CASOS, 1):
    print(f"\n{'─' * 80}")
    print(f"{caso['nome']}")
    print(f"Usuária: @{caso['perfil']['username']}")
    print(f"Bio: {caso['perfil']['bio']}")
    print(f"{'─' * 80}")

    start = time.time()
    try:
        resultado, usage = avaliar(caso["perfil"])
        elapsed = time.time() - start

        # Custo aprox (Haiku 4.5: $1/MTok input, $5/MTok output)
        cost = (usage.input_tokens * 1 + usage.output_tokens * 5) / 1_000_000
        total_cost += cost

        print(f"⏱  {elapsed:.1f}s · {usage.input_tokens}in/{usage.output_tokens}out · ${cost:.4f}")
        print()
        print(f"Score:     {resultado['score']}/10  ({resultado['confianca']})")
        print(f"Status:    {resultado['status']}")
        print(f"Razões:")
        for r in resultado.get("razoes", []):
            print(f"  • {r}")
        if resultado.get("sinais"):
            print(f"Sinais:    {', '.join(resultado['sinais'])}")
        if resultado.get("mensagem"):
            print(f"\nMENSAGEM PRONTA:")
            print(f"  ┌{'─' * 60}")
            for linha in resultado["mensagem"].split("\n"):
                print(f"  │ {linha}")
            print(f"  └{'─' * 60}")
            print(f"  ({len(resultado['mensagem'])} caracteres)")

    except Exception as e:
        print(f"❌ ERRO: {e}")

print(f"\n{'=' * 80}")
print(f"TOTAL: ${total_cost:.4f} para {len(CASOS)} perfis")
print(f"Projeção 500 perfis/semana: ${total_cost * 100:.2f}/semana")
print(f"{'=' * 80}")
