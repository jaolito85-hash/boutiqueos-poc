"""
Agente Qualificador — versão OpenAI
Mesma lógica da versão Anthropic, adaptada pra gpt-4o-mini.

Diferenças importantes:
- OpenAI usa system+messages parecido com Anthropic, conversão é direta
- Vamos usar JSON mode (response_format) pra garantir output válido
- Custo: ~$0.002 por perfil (vs ~$0.005 da Anthropic) — ainda mais barato
"""

import os
import json
import time
from openai import OpenAI

client = OpenAI()
MODEL = "gpt-4.1-mini"  # melhor adesao a instrucoes negativas, ~$0.005/perfil

# ============================================================================
# SYSTEM PROMPT (idêntico à versão Anthropic — o prompt é o cérebro, não muda)
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
- score 5-6: REVISAR (Fernanda decide manualmente)
- score < 5: DESCARTAR (não enviar)

# REGRAS ESPECIAIS DE PONTUAÇÃO (CRITICAS — leia com atenção)

REGRA 1 — BIO OURO (score minimo 7, vira APROVAR):
Se a bio menciona QUALQUER UMA destas palavras de forma direta, score MINIMO 7:
- "mesa posta", "mesaposta"
- "decoração", "decoradora", "designer de interiores", "arquiteta", "arquitetura"
- "lar", "minha casa", "cantinho", "anfitriã", "recebe"
- Categoria de Instagram "Digital creator" focada em LifeStyle/Casa
Importante: bio dizendo isso JA É evidencia. Nao exija tambem ter posts mostrando.

REGRA 2 — LOCALIZACAO + NICHO (score minimo 7, APROVAR):
Se a bio cita Maringa/Curitiba/Cascavel E qualquer sinal de casa/decoracao/lar, score MINIMO 7.
A localizacao certa + nicho certo = cliente confirmada. Nao precisa de mais nada.

REGRA 3 — PROFISSIONAIS DA CASA (score minimo 7, APROVAR):
Arquitetas, Designers de Interiores, Decoradoras sao publico aspiracional perfeito da haus.
Mesmo sem mencionar mesa posta, elas SAO clientes (porque indicam pra clientes delas tambem).
Bio com "Designer de Interiores" / "Arquiteta" + Curitiba/Maringa → APROVAR.

REGRA 4 — LOJAS-ALVO (score minimo 5, vai pra REVISAR):
Se segue 2 ou mais lojas-alvo, score MINIMO 5 (REVISAR, nunca DESCARTAR).

REGRA 5 — SO DESCARTE perfis com SINAL CLARO de nao-cliente:
- Conta comercial/atacado/revenda
- Profissao claramente fora do nicho (hair stylist, dentista, advogado)
- Menor de idade
- Localizacao confirmada FORA do raio
NA DUVIDA, REVISAR. Nunca descarte por "falta de evidencia" — isso vira REVISAR.

# REGRAS DA MENSAGEM (CRÍTICAS)

Quando aprovado, escreva uma mensagem que SIGA TODAS estas regras:

1. ABERTURA específica e VERDADEIRA — Cite UM detalhe real do perfil (algo que você viu). Pode ser: estilo da decoração, hobby visível, post recente, cidade. NUNCA invente. Se não tem detalhe claro, use status REVISAR.

2. TOM acolhedor e premium, com PRIMEIRA LETRA MAIÚSCULA — sempre comece a mensagem com letra maiúscula: "Oi Carol,", "Priscila,", "Vi seu perfil...". Use NOME PRÓPRIO capitalizado (Priscila, não "priscila"). Não use "Olá Senhora" ou "Prezada" (formal demais). Sem "tudo bem?" no início (genérico).

3. CONEXÃO em 1 frase — "tinha tudo a ver com a haus" / "lembrei daqui" / "achei que ia gostar". Conecta o detalhe dela com o universo da haus.

4. CONVITE explícito mas LEVE — Menciona o grupo VIP de WhatsApp. Coloca o link: vip-haus.vercel.app. Termina com algo como "se quiser dar uma olhada" — sem pressão.

5. NUNCA NUNCA NUNCA use estas palavras/frases (queimam o premium):
   - "adorei", "amei", "adoreeei", "que lindo", "linda demais", "fofo"
   - "muito a cara da", "tudo a ver", "combina com você"
   - "isso é muito"  / "ficou linda"
   - Preço, oferta, desconto, "promoção", "ofertão"
   - "vou te adicionar" (assediador)
   - 3+ emojis (1 é OK só se a cliente usa emoji)
   - Mais que 4 linhas

# VOCABULÁRIO HAUS (use uma destas palavras em CADA mensagem aprovada)

A mensagem precisa ter pelo menos UMA dessas palavras pra carregar identidade premium:
- copa, mesa posta, anfitriã, recebe, ritual
- curadoria, peça, coleção, atemporal, exclusivo
- toile, porcelana, louça
- cantinho (no contexto de cantinho da casa)
- ar (no contexto de "tem um ar de..." sofisticação)

# VARIAÇÃO DE ABERTURAS (não use sempre a mesma estrutura)

Rotacione entre estes estilos de abertura (escolha um por mensagem, varie entre perfis):
A) "Oi [Nome], vi seu/sua [detalhe] e [reação curta]..."
B) "[Nome], passei pelo seu perfil e [observação específica]..."
C) "Oi [Nome], seu [coisa específica] me lembrou da haus..."
D) "[Nome], que [detalhe específico] esse — tinha tudo a ver com o universo haus..."

Note: SEMPRE primeira letra MAIÚSCULA e nome próprio capitalizado.
Não comece com "adorei", "amei", "que linda" — começa com observação concreta.

6. ASSINATURA — Sempre termina com "— Fernanda, da haus" em linha separada

# COMPRIMENTO E TOM

- Total: 250-400 caracteres
- 2 a 4 linhas
- Português brasileiro, descontraído mas não baixo
- Como uma amiga educada faria, não vendedora

# SINAIS PRA SEGMENTAÇÃO

Detecte e marque tags úteis pra futuro. SÓ MARQUE SE TIVER EVIDÊNCIA DIRETA E LITERAL — nunca infira ou interprete metáforas:

- mae → SÓ se ela cita filhos pelo nome OU mostra foto de criança como dela
- recebe_casa → SÓ se ela posta literalmente mesa posta, jantar, brunch
- esteta → SÓ se ela mostra cantinhos curados da casa
- presenteadora → SÓ se ela posta presentes que deu ou recebeu
- aspiracional → SÓ se ela cita marcas premium específicas (L'Occitane, Le Creuset, etc)
- noiva_proxima → SÓ se ela está LITERALMENTE planejando casamento agora (vestido, lista de convidados, data marcada). NUNCA marcar por frases brincalhonas como "vou casar de novo com o mesmo" ou "casaria de novo" — essas são piadas românticas com o marido, não significam noivado real.
- localizacao_maringa / localizacao_curitiba / localizacao_cascavel / localizacao_outras

Princípio: se você precisa interpretar pra marcar, NÃO marca. Melhor errar pra menos do que alucinar.

# CONFIANÇA

- HIGH: bio clara, fotos com contexto, várias evidências
- MEDIUM: alguns sinais mas dados parciais
- LOW: perfil cinza, pouca informação

# FORMATO DE SAÍDA

Devolva APENAS um JSON válido, sem texto antes ou depois, neste schema exato:

{"score": 8, "status": "APROVAR", "razoes": ["..."], "mensagem": "...", "sinais": ["..."], "confianca": "HIGH"}

Importante:
- Se status for REVISAR ou DESCARTAR, mensagem deve ser null
- Se confianca for LOW, prefira REVISAR sobre APROVAR
- A mensagem deve soar como AMIGA EDUCADA com cara de curadora, não como VENDEDORA
- Cada mensagem é única — não use a mesma estrutura/palavra de abertura entre perfis
- Use o vocabulário haus (copa, anfitriã, ritual, peça, etc) — pelo menos uma palavra em cada mensagem aprovada
- A leitora ideal é uma mulher de 35 anos que mora num apartamento bonito e bebe chá de tarde. Imagine ela lendo a mensagem antes de escrever.

# CHECKLIST DE VALIDAÇÃO FINAL (faça mentalmente antes de devolver o JSON)

Antes de retornar a resposta, percorra esta checklist palavra por palavra na sua mensagem:

1. A palavra "adorei" aparece? Se sim, REESCREVA usando uma destas: "tem o ar de", "me lembrou", "respira", "tem cara de", "passei pelo seu perfil e", "vi seu/sua".

2. A expressão "tudo a ver com" aparece? Se sim, REESCREVA usando: "conversa com", "tem o ar de", "respira a estética de", "lembra a curadoria da", "é exatamente o universo".

3. A palavra "amei" / "adorei" / "linda" / "lindo" / "lindo demais" aparece em qualquer forma? Se sim, REMOVA e use observação concreta no lugar.

4. A mensagem começa com "adorei", "que linda" ou começa com letra MINÚSCULA? Se sim, REESCREVA: comece com letra MAIÚSCULA + nome próprio capitalizado + observação concreta (use uma das 4 estruturas em VARIAÇÃO DE ABERTURAS).

5. Existe pelo menos UMA palavra do vocabulário haus na mensagem (copa, anfitriã, ritual, curadoria, peça, atemporal, mesa posta, exclusivo, toile, porcelana, cantinho, ar, recebe)? Se NÃO, reescreva incluindo uma.

6. Você está marcando o sinal "noiva_proxima"? Só marque se a pessoa estiver LITERALMENTE planejando casamento agora (vestido, lista de convidados, data). Frases brincalhonas como "vou casar de novo com o mesmo" NÃO contam — são piadas românticas com marido.

SÓ DEPOIS de passar nessa checklist, devolva o JSON.
"""

# ============================================================================
# FEW-SHOT EXAMPLES (idênticos à versão Anthropic)
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
            "mensagem": "Oi Vanessa, seu cantinho de café da manhã tem o ar que a gente cuida aqui na haus — peças pra quem valoriza receber em casa. Solto novidades primeiro no grupo VIP. Dá uma olhada: vip-haus.vercel.app\n\n— Fernanda, da haus",
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
    """Monta histórico de mensagens com few-shots + perfil real."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for shot in FEW_SHOTS:
        msgs.append({"role": "user", "content": json.dumps(shot["input"], ensure_ascii=False)})
        msgs.append({"role": "assistant", "content": json.dumps(shot["output"], ensure_ascii=False)})
    msgs.append({"role": "user", "content": json.dumps(perfil_real, ensure_ascii=False)})
    return msgs


def avaliar(perfil):
    """Chama OpenAI e devolve o JSON parseado."""
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=800,
        temperature=0.75,
        response_format={"type": "json_object"},  # OpenAI: força output JSON válido
        messages=build_messages(perfil)
    )
    text = response.choices[0].message.content.strip()
    return json.loads(text), response.usage


# ============================================================================
# CASOS DE TESTE (idênticos à versão Anthropic)
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
print(f"TESTE DO AGENTE QUALIFICADOR — haus tableware ({MODEL})")
print("=" * 80)

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

        # Custo gpt-4o-mini: $0.15/MTok input, $0.60/MTok output
        cost = (usage.prompt_tokens * 0.40 + usage.completion_tokens * 1.60) / 1_000_000
        total_cost += cost

        print(f"⏱  {elapsed:.1f}s · {usage.prompt_tokens}in/{usage.completion_tokens}out · ${cost:.4f}")
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
        print(f"❌ ERRO: {type(e).__name__}: {e}")

print(f"\n{'=' * 80}")
print(f"TOTAL: ${total_cost:.4f} para {len(CASOS)} perfis")
print(f"Projeção 500 perfis/semana: ${total_cost * 100:.2f}/semana (~R$ {total_cost * 100 * 5:.0f}/semana)")
print(f"{'=' * 80}")
