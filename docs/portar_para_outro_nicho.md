# Como portar o esqueleto pra outro nicho/produto

> Documento prático: o que reaproveitar, o que customizar, em que ordem evoluir o template.
> Audiência: você (founder) decidindo se forka manual, vira template ou SaaS multi-tenant.

## 1. Resposta curta

**Hoje (1 a 3 clientes novos):** *Fork manual* + **brand pack** centralizado. Você duplica o repo, troca **~15 pontos de customização** e roda. Estimativa: **1-2 dias de setup por cliente novo**.

**Médio prazo (4 a 10 clientes):** Refatorar a customização pra `config/brand.yaml` (uma chave por marca). Mesmo código, marcas diferentes via arquivo de config. **Estimativa: 1 sprint de refactor**.

**Longo prazo (10+ clientes ou time vendendo SaaS):** Multi-tenant — um único deploy serve N marcas via login + isolamento por `tenant_id` no SQLite/Postgres. **Esforço alto, ~2 meses**, só faz sentido quando vender como produto.

A seção 7 detalha a evolução. **Comece pelo Fork manual** — é o que entrega valor primeiro e te ensina o que é realmente reaproveitável.

## 2. Anatomia do projeto — Core × Customização

### 🟢 Core reaproveitável (não tocar entre nichos)

| Componente | Por quê é genérico |
|---|---|
| **Pipeline outbound** (`02_scraper.py` → `03_pipeline.py`) | Scraping Apify + qualificação OpenAI funciona pra qualquer nicho |
| **Modelo de dados** (`database.py` schema base) | Prospects, events, products, orders, competitor_snapshots — universais |
| **Blueprint pattern Flask** | Estrutura de API REST genérica |
| **Helpers internos** | `_get_client()` lazy, throttle diário (`*_runs` tables), UTF-8 stdout, migrations idempotentes |
| **Módulo `competitor_intel`** | Análise estratégica de qualquer concorrente IG — só muda o contexto no prompt |
| **Módulo `sales` + `orders`** | LTV, ticket médio, recompra — universais |
| **Módulo `insights`** | Funil, taxa de resposta, by-source, by-intent — universais |
| **Módulo `meta_ads`** (Sprint 2) | Custom Audience + Lookalike + boost — só muda Ad Account |
| **UI scaffolding** (`painel/index.html` estrutura) | Tabs, modal, toast, fmtBRL, escapeHtml |
| **CLI scripts numerados** (`0X_*.py` padrão) | Convenção, não conteúdo |
| **`packager.py`** | Empacotamento ZIP de produto pra postagem |

### 🟡 Customização por marca/nicho (são esses pontos que mudam)

Catalog rápido (executei `grep "haus|Umuarama|tableware"` no projeto — encontrou 25 arquivos):

| Arquivo / local | O que customizar | Tipo de mudança |
|---|---|---|
| `agente-qualificador.md` | Tom, critérios de score, exemplos few-shot, persona da Aline → persona equivalente | **Texto** |
| `caption_gen.py` (SYSTEM_PROMPT) | Contexto da marca, mix de produtos, faixa de preço, cliente típica, CTA | **Texto** |
| `competitor_prompt.py` | "Especializada em tableware/decoração premium" → especializada no nicho-X | **Texto** |
| `leads_import.py` (TEMPLATES_MENSAGEM) | 3 templates de DM com nome da loja + cidade | **Texto** |
| `test_agente_openai.py` / `test_agente.py` | 4 exemplos few-shot baseados em DMs reais do nicho | **Texto** |
| `lojas_alvo.json` | 10 perfis IG de concorrentes pra scrapar followers | **JSON** |
| `links.py` (`GRUPO_VIP_URL`) | `vip-haus.vercel.app` → URL do CTA do cliente novo | **String** |
| `database.py` (schema) | Categorias hardcoded (`porcelana`, `le_creuset`, `box_presente`, `orquidea`) | **SQL enum** |
| `painel/index.html` (`<title>` + `header`) | "haus studio · Painel interno" → branding novo | **HTML** |
| `painel/index.html` (variáveis CSS) | `--bg/--sage/--gold` palette + Fraunces/Outfit fonts | **CSS** |
| `templates/hyperframes/showcase_t1/` | Template HyperFrames do reel (tokens já parametrizados) | **HTML+CSS** |
| `image_engine.py` | Prompts de geração de imagem (refs à estética premium) | **Texto** |
| `.env` | Tokens Apify, OpenAI, Meta — uma conta por cliente | **Credenciais** |
| `prospects.db` | Banco zerado por cliente | **Reset** |

**Total estimado:** ~15 pontos focais de customização. Quase tudo é texto/config.

## 3. Forma recomendada AGORA: Fork + Brand Pack

### Passo 1 — Centralizar a customização num único módulo `brand.py`

Crie em cada projeto novo o arquivo `brand.py` (substituindo strings hardcoded espalhadas):

```python
# brand.py — Tudo que é específico desta marca está aqui.
# Trocou de cliente? Edita só este arquivo + lojas_alvo.json + .env.

MARCA = {
    "nome": "haus tableware",
    "nome_curto": "haus",
    "operadora": "Aline",                          # quem assina DMs
    "operadora_assinatura": "— Aline, da haus",
    "cidade_principal": "Umuarama, PR",
    "cidades_alvo": [
        "Umuarama", "Cianorte", "Maringá", "Toledo",
        "Cascavel", "Foz do Iguaçu", "Paranavaí", "Campo Mourão",
    ],
}

# Posicionamento + voz (alimenta caption_gen, competitor_prompt, agente-qualificador)
POSICIONAMENTO = """\
Loja-boutique premium de mesa posta, decoração e presentes.
Mix: porcelana, Le Creuset, fragrâncias L'Occitane/Lenvie, eletros Ariete, boxes presenteáveis.
Cliente típica: mulher 28-55, classe média-alta, valoriza casa bem cuidada e mesa posta.
Faixa de preço: R$ 40 (vela) → R$ 1.500+ (peças premium).
Tom: editorial, poético-prático. Nunca usa "promoção", "queima", "imperdível".
"""

# CTA padrão (UTM tracking já é gerado por links.cta_link)
CTA_URL = "vip-haus.vercel.app"
CTA_TIPO = "grupo VIP no WhatsApp"               # "lista VIP", "comunidade", "newsletter"...

# Catálogo — categorias permitidas no produto (UI + validação)
CATEGORIAS_PRODUTO = [
    "porcelana", "le_creuset", "box_presente",
    "orquidea", "caixa_decorativa", "fragrancia", "outro",
]

# Estética visual (variáveis CSS injetadas no painel/index.html)
PALETA = {
    "bg":          "#F7F4EE",
    "bg_card":     "#FFFFFF",
    "ink":         "#1F2426",
    "sage":        "#8B9A82",
    "sage_deep":   "#5D6E55",
    "gold":        "#B8A876",
    "danger":      "#C77B5C",
    "success":     "#5D8A6B",
}
FONTES = {
    "display": "Fraunces",        # serif italic, headings + números
    "body":    "Outfit",          # sans, corpo
}

# Regras de negócio
DAILY_DM_LIMIT = 15               # protocolo de segurança da Aline
DAILY_META_CAMPAIGN_LIMIT = 3
COMPETITOR_DAILY_LIMIT = 10
```

Depois, em cada arquivo onde aparece string da Haus, importe e use:

```python
# Antes (caption_gen.py)
SYSTEM_PROMPT = """Você é a Curadora de Conteúdo da haus tableware ..."""

# Depois
from brand import MARCA, POSICIONAMENTO
SYSTEM_PROMPT = f"""Você é a Curadora de Conteúdo da {MARCA['nome']}.

{POSICIONAMENTO}

Operadora: {MARCA['operadora']} (assinatura padrão: '{MARCA['operadora_assinatura']}')
..."""
```

Faz a mesma mudança em: `competitor_prompt.py`, `leads_import.py`, `agente-qualificador.md` (vira `agente-qualificador.template.md` com placeholders), `image_engine.py`, `links.py`, `database.py` (categorias), `painel/index.html` (CSS variables + `<title>`).

**Esforço inicial:** 4-6h pra extrair tudo. Depois desse refactor, **trocar de cliente = editar `brand.py` + `lojas_alvo.json` + `.env` + branding visual**.

### Passo 2 — Checklist por cliente novo

```
[ ] Copiar repo: git clone projeto-outbound-haus projeto-outbound-<cliente>
[ ] Reset banco: rm prospects.db (init_db roda no primeiro python run.py)
[ ] Editar brand.py com dados do cliente
[ ] Editar lojas_alvo.json com perfis IG de concorrentes do nicho
[ ] Editar agente-qualificador.template.md com:
    - 4 exemplos few-shot novos baseados em DMs reais do nicho
    - Critérios de score adaptados (o que define "lead quente" no nicho-X)
[ ] Criar landing equivalente ao vip-haus.vercel.app (ou apontar CTA pra outro lugar)
[ ] Criar conta Apify + OpenAI + Meta Business do cliente
[ ] Preencher .env com credenciais novas
[ ] Trocar templates/hyperframes/showcase_t1/ se a estética muda muito
[ ] Trocar fontes Google Fonts no painel/index.html (head) se PALETA muda
[ ] Rodar python run.py — abre painel
[ ] Rodar python 02_scraper.py — primeira leva de prospects
[ ] Rodar python 03_pipeline.py — qualifica
[ ] Aline (ou equivalente) entra no painel e começa a operar
```

**Tempo estimado por cliente novo após o refactor inicial:** 1-2 dias úteis (a maior parte é tempo de criar contas Apify/Meta, não código).

### Passo 3 — O que MELHORAR ao longo do tempo

Cada cliente novo vai expor algo que não estava parametrizado. Padrão:

1. Primeira vez que mexer numa string hardcoded, **promova ela pro `brand.py`**.
2. Não esperar a 3ª cliente pra refatorar — refatore na 2ª.
3. Anote no `CHANGELOG-template.md` do projeto template tudo que foi parametrizado a cada cliente.

## 4. O que NÃO recomendo (anti-padrões)

❌ **Variáveis de ambiente pra branding** (`HAUS_BRAND_NAME=...`). Polui o `.env` e mistura credencial com config de produto.

❌ **Fork manual sem brand.py centralizado.** No 3º cliente você vai estar caçando "haus" nos arquivos com grep e errando — perde 1 dia toda vez.

❌ **Multi-tenant antes do 3º cliente.** Você não sabe ainda o que vai mudar entre nichos. Refatorar pra multi-tenant cedo demais cria abstrações erradas.

❌ **Manter 3 repos com mesmo código divergindo.** Quando você adicionar Meta Ads no projeto Haus, vai querer que os outros clientes também recebam. Mantenha **1 repo "core" + branch/diretório por cliente**, ou um repo template com `git remote update template` workflow.

## 5. Estratégia de repos recomendada (1 a 5 clientes)

```
github.com/seu-org/
├── outbound-engine-template/         ← núcleo público (ou privado)
│   ├── brand.py                       (template com placeholders)
│   ├── lojas_alvo.json                (vazio)
│   ├── README-portar.md               (este doc)
│   └── ... resto do código ...
│
├── outbound-haus/                     ← cliente 1 (atual)
│   ├── brand.py                       (preenchido pra haus)
│   ├── lojas_alvo.json                (10 lojas tableware PR)
│   ├── .env                           (creds haus)
│   └── ... resto ...
│
├── outbound-loja-fitness/             ← cliente 2 (hipotético)
│   ├── brand.py                       (preenchido pra fitness)
│   └── ...
```

**Workflow de atualização do template pros clientes:**

```bash
# No projeto cliente, adiciona o template como remote
git remote add template https://github.com/seu-org/outbound-engine-template

# Quando o template ganha nova feature (ex: Meta Ads):
git fetch template
git merge template/main --strategy-option=theirs --allow-unrelated-histories
# Conflitos: aceita do template, exceto em brand.py / .env / lojas_alvo.json
```

Não é perfeito (git de baixo nível), mas é **drasticamente mais simples** que multi-tenant e mantém clientes em sincronia.

## 6. Quando vale pular pra Multi-Tenant SaaS

**Sinais claros:**
- 10+ clientes ativos pagando.
- Você está perdendo > 1 dia/cliente em deploy/upgrade manual.
- Já tem 3 features no template que cada cliente "ativa ou não" — virou flag de produto.
- Tem time (não só você).

**O que muda na arquitetura:**

| Hoje (1 deploy/cliente) | Multi-tenant |
|---|---|
| `prospects.db` na máquina da Aline | Postgres único, coluna `tenant_id` em toda tabela |
| `brand.py` por repo | Tabela `tenants(id, brand_config_json)` no DB |
| `.env` por máquina | Secrets manager (AWS, Doppler) por tenant |
| Aline acessa `localhost:8000` | Aline faz login em `app.outbound-engine.com/haus` |
| Sem auth | OAuth + roles (operador, dono, admin) |
| Flask local | Flask + Gunicorn + Cloudflare + worker queue pra Apify async |
| Cron por máquina | Celery beat / cron centralizado por tenant |
| Painel renderiza com cores hardcoded | Painel carrega `brand_config_json` do tenant no boot |

**Esforço:** ~2 meses pra um dev sênior. Não comece esse caminho sem 5+ clientes pedindo a mesma coisa.

## 7. Roadmap recomendado pra você

| Fase | Quando | Esforço | Entrega |
|---|---|---|---|
| **F1 — Fork manual** | Hoje (1º cliente extra) | 1-2 dias | Cópia do repo Haus, customizada à mão |
| **F2 — Extrair `brand.py`** | Logo antes do 2º cliente | 4-6h | Todo string Haus vira import do brand.py |
| **F3 — Template + merge workflow** | Antes do 3º cliente | 2-3 dias | Repo template separado, clientes recebem features novas via git merge |
| **F4 — Painel troca branding via config** | Antes do 5º cliente | 1 dia | CSS variables injetadas via Jinja a partir do `brand.PALETA` |
| **F5 — Multi-tenant SaaS** | 10+ clientes | 2 meses | 1 deploy serve todos, auth, billing, etc |

Você está na fronteira entre F1 e F2. **Faça F2 antes de adicionar o 2º cliente** — é o investimento de melhor retorno.

## 8. Custos por cliente novo (operação)

Independente da fase, cada cliente extra tem custos:

| Serviço | Custo/mês | Notas |
|---|---|---|
| Apify | $5-30 | Depende do volume de scraping |
| OpenAI | $5-50 | gpt-4.1-mini é barato (~$0.005/perfil qualificado) |
| Meta Ads | R$ 300-500 inicial | Orçamento do cliente, não build |
| Hosting do painel | $0 (local) | Vai pra ~$15-30/mês quando virar SaaS (Vercel/Railway) |
| Landing (vip-haus.vercel.app equiv) | $0 (Vercel free) | Já tem |

**Margem realista:** se você cobrar R$ 1.500-3.000/mês por cliente operando esse stack, custos diretos ficam < 10% do faturamento.

## 9. Nichos que mais aproveitam esse stack

O esqueleto foi desenhado pra **boutique 1-a-1 com ticket médio alto + venda via DM/grupo**. Funciona muito bem pra:

| Nicho | Por que encaixa | Adaptações principais |
|---|---|---|
| **Moda autoral / brechó premium** | Ticket alto, venda DM, estética importa | Categorias, mix de produto |
| **Decoração / mobiliário sob medida** | Ticket alto, atendimento consultivo | Tom mais sóbrio, menos "achadinho" |
| **Joalheria artesanal** | Venda 1-a-1, exclusividade | Estética minimalista, sem promoção |
| **Pet boutique (rações premium, acessórios)** | Recompra alta, comunidade engajada | Cliente é o tutor, não o pet |
| **Cosméticos artesanais/clean beauty** | Recompra, comunidade VIP | Pode trocar grupo VIP por lista de email |
| **Confeitaria autoral / panettone** | Sazonal, pedido sob encomenda | Adicionar campo `prazo_entrega` |
| **Coaches/consultores premium** | Venda consultiva, alto ticket | Trocar "produto" por "pacote de mentoria" |

**Não encaixa bem:**
- E-commerce massivo (>1000 SKUs ativos) — `products` foi pensado pra catálogo curado
- B2B enterprise (cycle de venda 6+ meses) — Aline opera em ciclo curto
- Mass market low-ticket — economia do outbound 1-a-1 não fecha

## 10. Quick checklist da Fase 2 (extrair `brand.py`)

Se você decidir fazer F2 agora — antes do 2º cliente — segue o roteiro:

```
[ ] criar brand.py com dicts MARCA, POSICIONAMENTO, CTA_URL, CATEGORIAS_PRODUTO, PALETA, FONTES
[ ] caption_gen.py:           importar e f-string no SYSTEM_PROMPT
[ ] competitor_prompt.py:     importar POSICIONAMENTO no SYSTEM_PROMPT
[ ] leads_import.py:          importar MARCA pros 3 templates
[ ] links.py:                 importar CTA_URL (substituir constante)
[ ] database.py:              importar CATEGORIAS_PRODUTO (não hardcode no SQL)
[ ] image_engine.py:          importar POSICIONAMENTO nos prompts
[ ] painel/index.html:        substituir <title>, header, e CSS variables via Jinja2 (Flask render_template)
[ ] agente-qualificador.md:   virar agente-qualificador.template.md, processado em runtime
[ ] templates/hyperframes:    tokens já parametrizados (PRODUCT_NAME, COLLECTION_LABEL, PRICE, CTA_URL) — só ajustar visual
[ ] README-portar.md:         este doc fica no template repo
[ ] CHANGELOG-template.md:    "v1.0 — extração inicial de brand.py"
```

Estimativa: **uma tarde**. Próximo cliente vai levar 1 dia em vez de 1 semana.

---

## TL;DR

1. **Sim, dá pra reusar tudo isso pra outros nichos.**
2. **Forma recomendada AGORA:** Fork manual + criar `brand.py` centralizando os ~15 pontos de customização.
3. **Antes do 2º cliente:** invista 4-6h fazendo F2 (extrair `brand.py`) — o retorno é gigantesco.
4. **Antes do 3º cliente:** vire repo template com merge workflow git.
5. **Só vire SaaS multi-tenant com 10+ clientes confirmados.**
6. **Tempo por cliente novo após F2:** 1-2 dias (maior parte criando contas Apify/Meta).
7. **Custo direto:** < R$ 200/mês/cliente.

Quer que eu já comece a Fase 2 e extraia o `brand.py` no projeto Haus atual? Fica como template-zero pra os próximos.
