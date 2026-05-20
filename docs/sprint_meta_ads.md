# Sprint Meta Ads — Tráfego pago integrado ao painel haus

> Versão 1 · escrita após Sprint 1 (Vendas + UTM tracking) entregue.
> Stack: Python + Flask + SQLite + facebook-business SDK (oficial Meta).
> Não usa MCP custom da Meta — uso é server-side, painel da Aline é a interface humana.

## 1. Por que essa sprint

Hoje o painel da haus mede o funil orgânico ponta a ponta — DM enviada → resposta → entrou no grupo VIP → comprou. Com a tabela `orders` em produção, dá pra responder *"quem é meu melhor cliente, quanto vale o LTV dela, por qual canal entrou"*.

O próximo salto é **alimentar o Meta Ads com essa inteligência** em vez de chutar audiência por interesses genéricos. Quem virou cliente sai do `orders.valor_brl` direto pra uma Custom Audience na Meta, e a partir dela monta-se Lookalike value-based — audiência muito mais qualificada que qualquer "mulheres 28-55 interessadas em decoração".

**Entrega-fim:** Aline consegue, em 1 clique no painel:

1. **Sincronizar** clientes pra Custom Audience da Meta (hash de WhatsApp/email).
2. **Criar Lookalike 1%** value-based usando o LTV como peso.
3. **Impulsionar um post** orgânico do Instagram pra essa Lookalike (budget configurável).
4. **Ver no dia seguinte** quanto gastou, quantos clicaram no `vip-haus.vercel.app`, e qual o ROAS estimado cruzando com `orders.utm_source = 'meta_ad'`.

## 2. Pré-requisitos (setup Meta — Dia 0)

Status atual confirmado com o user: **"não sei / preciso checar"**. Esse checklist serve como runbook prático. Aline (ou o user) executa ou delega; sem isso o código da sprint não tem onde rodar.

### Ordem de execução

1. **Conta Instagram comercial** — em `instagram.com/account/professional`, mudar pra Business. Vincular à Página Facebook da haus.
2. **Página Facebook** — `facebook.com/business/help/473994396650734`. Já deve existir; confirmar que está ligada ao IG business.
3. **Business Manager** — `business.facebook.com` → criar novo Business Portfolio "haus tableware". Adicionar a Página e o IG dentro dele.
4. **Ad Account** — dentro do Business Manager → *Contas > Contas de Anúncios > Adicionar > Criar*. Moeda BRL, fuso BRT.
5. **App de desenvolvedor** — `developers.facebook.com/apps` → criar app tipo "Business". Nome: `haus-outbound`. Adicionar produto **"Marketing API"** em *Add Products*.
6. **System User** — Business Manager → *Configurações > Usuários do sistema > Adicionar*. Nome: `haus-painel`. Role: Admin. Gerar token com permissões:
   - `ads_management`
   - `ads_read`
   - `business_management`
   - `pages_read_engagement`
   - `instagram_basic`
   - `instagram_manage_insights`
7. **Pixel** — Events Manager → criar Pixel `vip_haus_pixel`. Instalar snippet no projeto `vip-haus.vercel.app` (1 arquivo HTML, antes de `</head>`). Validar via *Test Events* abrindo o site.
8. **Conversions API token** (opcional MVP) — Events Manager → Pixel → *Configurações > API de conversões > Gerar token*. Guardar.

### Variáveis no `.env` (todas opcionais — sem elas, aba "Tráfego" mostra estado "desconectado")

```
META_APP_ID=...
META_APP_SECRET=...
META_SYSTEM_USER_TOKEN=...
META_AD_ACCOUNT_ID=act_XXXXXXXXX
META_PAGE_ID=...
META_IG_BUSINESS_ID=...
META_PIXEL_ID=...
META_CONVERSIONS_API_TOKEN=...
HAUS_META_DAILY_CAMPAIGN_LIMIT=3
```

### Script de validação: `08_meta_check.py`

CLI numerada, segue convenção dos `0X_*.py`. Roda `meta_ads.health_check()` e imprime status humano:

```
$ python 08_meta_check.py
✓ Token válido (System User: haus-painel)
✓ Ad Account act_123 · saldo restante R$ 487 · moeda BRL
✓ Page conectada: 'haus tableware' (id 100xxx)
✓ Instagram Business conectado: @hausdecasa.tableware
✓ Pixel 'vip_haus_pixel' (id 5xxx) — último ping há 2h
⚠ Conversions API token: ausente (opcional)
```

Falha amigável com instrução de correção se algo está faltando.

## 3. Modelo de dados — 3 tabelas novas

Adicionar em `database.py` (SCHEMA + migrations idempotentes seguindo padrão atual). Helpers Python correspondentes vão no mesmo arquivo.

```sql
CREATE TABLE IF NOT EXISTS meta_audiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_audience_id TEXT UNIQUE,            -- id retornado pela Meta
    nome TEXT NOT NULL,                      -- 'haus_customers_v1', 'haus_ltv_top_lookalike_1pct_br'
    tipo TEXT,                               -- 'custom' | 'lookalike' | 'website_retarget'
    fonte TEXT,                              -- 'customers_all' | 'customers_top_ltv' | 'pixel_vip_haus' | 'lookalike_seed_<id>'
    seed_audience_id INTEGER,                -- FK opcional p/ seed quando tipo='lookalike'
    tamanho_aproximado INTEGER,
    last_sync_at TIMESTAMP,
    status TEXT DEFAULT 'active',            -- 'active' | 'archived' | 'processing'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seed_audience_id) REFERENCES meta_audiences(id)
);

CREATE INDEX IF NOT EXISTS idx_meta_aud_tipo ON meta_audiences(tipo);

CREATE TABLE IF NOT EXISTS meta_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_campaign_id TEXT UNIQUE,
    nome TEXT NOT NULL,
    objective TEXT,                          -- 'OUTCOME_TRAFFIC' | 'OUTCOME_ENGAGEMENT' | 'OUTCOME_AWARENESS'
    status TEXT,                             -- 'ACTIVE' | 'PAUSED' | 'DELETED'
    budget_diario_brl REAL,
    audience_id INTEGER,                     -- FK -> meta_audiences
    product_id INTEGER,                      -- FK opcional -> products
    media_id INTEGER,                        -- FK opcional -> product_media (criativo usado)
    boosted_post_url TEXT,                   -- URL do post quando campanha é boost
    notes TEXT,                              -- notas livres pra Aline
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audience_id) REFERENCES meta_audiences(id),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (media_id) REFERENCES product_media(id)
);

CREATE INDEX IF NOT EXISTS idx_meta_camp_status ON meta_campaigns(status);

CREATE TABLE IF NOT EXISTS meta_ads_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    data DATE NOT NULL,
    impressions INTEGER,
    reach INTEGER,
    clicks INTEGER,
    spend_brl REAL,
    cpm_brl REAL,
    ctr REAL,
    link_clicks INTEGER,                     -- cliques no link de destino (vip-haus.vercel.app)
    profile_visits INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(campaign_id, data),
    FOREIGN KEY (campaign_id) REFERENCES meta_campaigns(id)
);

-- Throttle diário de criação de campanha (protege contra cliques acidentais)
CREATE TABLE IF NOT EXISTS meta_campaign_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date DATE NOT NULL,
    action TEXT,                             -- 'create_boost' | 'create_retarget'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_meta_runs_date ON meta_campaign_runs(run_date);
```

## 4. Arquivos novos (5 módulos Python + 1 blueprint)

| Arquivo | Responsabilidade |
|---|---|
| `meta_ads.py` | Wrapper sobre `facebook-business`. Lazy init. `health_check()`, `get_ad_account()`, `get_page()`, helpers comuns. |
| `meta_audiences.py` | Sync de audiências. `sync_customers_audience()` (Custom), `create_lookalike(seed_id, percent=1)` (value-based), `sync_website_retarget()`. |
| `meta_campaigns.py` | Criação e controle. `boost_post(url, budget_diario, dias, audience_id)`, `create_retarget_campaign(...)`, `pause_campaign(id)`, `resume_campaign(id)`. |
| `meta_insights.py` | Pull de métricas. `pull_daily_metrics()` itera campanhas ACTIVE, busca insights de ontem, upsert em `meta_ads_metrics`. |
| `meta_ads_panel.py` | Blueprint Flask, padrão `competitor_panel.py`. Endpoints REST listados na seção 5. |
| `08_meta_check.py` | CLI: valida credenciais e imprime status humano. |
| `09_meta_sync.py` | CLI cron diário. Executa `sync_customers_audience()` + `pull_daily_metrics()`. Documentado pra rodar `0 6 * * *` (06:00 BRT). |

**Dependência nova no `run.py`:**

```python
REQUIRED = [
    ...,
    ("facebook_business", "facebook-business"),
]
```

## 5. API REST

Blueprint `bp_meta` registrado em `04_painel.py`. Convenção `/api/trafego/...` (palavra de cliente — Aline entende, "Meta" é jargão).

```
GET    /api/trafego/status                # health_check + status conexão Meta
GET    /api/trafego/dashboard             # spend hoje + mês, conversões 7d, CPM, ROAS estimado
GET    /api/trafego/audiences             # lista audiences (custom + lookalike + retarget)
POST   /api/trafego/audiences/sync        # roda sync_customers_audience()
POST   /api/trafego/audiences/lookalike   # body: {seed_audience_id, percent=1}
GET    /api/trafego/campaigns             # lista campaigns + métricas agregadas dos últimos 7d
POST   /api/trafego/campaigns/boost-post  # body: {post_url, budget_diario, dias, audience_id}
POST   /api/trafego/campaigns/retarget    # body: {audience_id, budget_diario, dias, media_id?}
PATCH  /api/trafego/campaigns/<id>/pause
PATCH  /api/trafego/campaigns/<id>/resume
GET    /api/trafego/uso-hoje              # {used, limit, remaining, can_run} - throttle
GET    /api/trafego/concorrentes-ads      # ads ativos dos competitor handles (Apify)
```

**Throttle de criação:** `POST /campaigns/boost-post` e `POST /campaigns/retarget` consultam `meta_campaign_runs` do dia. Se `>=  HAUS_META_DAILY_CAMPAIGN_LIMIT` (default 3), retorna 429 com mensagem "Você já criou X campanhas hoje. Volte amanhã ou aumente o limite". Mesma proteção que `competitor_panel.py`.

**Tratamento de erro amigável** (cliente-facing, sem jargão Meta):
- Token expirado → "Conexão com Meta expirou. Avise a equipe Haus pra renovar."
- Sem saldo no Ad Account → "Saldo da conta de anúncios acabou. Recarregue em business.facebook.com."
- Audiência ainda processando → "Esta audiência ainda está sendo preparada pela Meta (~1h). Tente em alguns minutos."

## 6. UI — nova aba "Tráfego"

`data-tab="trafego"` no `painel/index.html`, posicionada entre **Vendas** e **Insights** (semanticamente: dados de cliente → dados de tráfego → analytics consolidados).

### Estado "desconectado" (sem `META_SYSTEM_USER_TOKEN` no `.env`)

Empty state com headline editorial + botão "Quero configurar" que abre modal com o runbook da seção 2. Não esconder a aba — é convite à expansão.

### Estado "conectado"

```
┌─ TOPO (4 stat cards) ────────────────────────────────────────────┐
│ Gasto mês     · Cliques VIP    · ROAS estimado · Audiência seed │
│ R$ 384,20     · 142            · 4.2x         · 1.847 pessoas   │
└──────────────────────────────────────────────────────────────────┘

┌─ AUDIÊNCIAS ────────────────────────────────────────────────────┐
│ haus_customers_v1            custom    │ 38 pessoas │ 2h atrás  │
│   ↳ Sincronizar agora                                            │
│ haus_ltv_top_lookalike_1pct  lookalike │ 1.847.000  │ ontem     │
│ haus_pixel_vip_30d           retarget  │ 412        │ 14m atrás │
└──────────────────────────────────────────────────────────────────┘

┌─ CAMPANHAS ATIVAS ──────────────────────────────────────────────┐
│ Boost · mesa toile · ATIVA │ R$ 23,40 hoje · 87 cliques · pausar │
│ Retarget VIP · ATIVA       │ R$ 12,80 hoje · 42 cliques · pausar │
└──────────────────────────────────────────────────────────────────┘

┌─ CTA principal: [+ Impulsionar post] ───────────────────────────┐
│ Modal: cola URL do post IG · slider budget R$10-100 · dias 1-30 │
│        dropdown audiência · confirma                             │
└──────────────────────────────────────────────────────────────────┘

┌─ ADS DOS CONCORRENTES (apenas leitura) ─────────────────────────┐
│ Grid de cards: thumbnail · texto · ativo desde · link Meta Lib  │
│ Lê do competitor_intel.coletar_ads_ativos(handle) (Apify)        │
└──────────────────────────────────────────────────────────────────┘
```

**Aesthetic:** mesma palette `--bg/--sage/--gold/--ink` + Fraunces italic em números (R$ 384,20, 1.847, 4.2x), Outfit light em labels. Cards com hover sutil seguindo padrão das abas Vendas/Concorrentes.

## 7. Casos de uso priorizados

Filtrei pelo contexto boutique 1-a-1, ticket alto, sem e-commerce:

| # | Caso de uso | Quando ativa | Por quê |
|---|---|---|---|
| 1 | Custom Audience clientes + Lookalike value-based 1% | Imediato — já tem `orders` | Audiência semente de qualidade alta (clientes reais com LTV ponderado). Multiplicador máximo. |
| 2 | Retargeting visitantes vip-haus.vercel.app (Pixel) | Após 7d de pixel ativo | Volume mínimo pra Meta processar. Recupera quem clicou mas não entrou no grupo. |
| 3 | Boost programático de posts top 10% engajamento | Manual via painel | Aumenta alcance de conteúdo que já provou converter organicamente. |
| 4 | Catalog Ads (Advantage+) por coleção | Sprint 3 — precisa SKU + estoque em `products` | Dinâmico, escala bem quando catálogo crescer. |

**Descartado pra boutique 1-a-1:**
- **Lead Ads de formulário** — quebra estética do funil íntimo "DM da Aline".
- **Conversion API server-side** — overkill no MVP. Pixel client-side cobre 80%.
- **Performance Max** — automação opaca, baixo controle de criativo. Não combina com marca premium.

## 8. Cronograma de implementação (5-7 dias úteis)

**Dia 0** — Setup Meta (Aline + user)
- Executar checklist da seção 2.
- Configurar `.env`.
- Rodar `python 08_meta_check.py` até passar.

**Dia 1** — Fundação backend
- Adicionar 3 tabelas + helpers em `database.py`.
- Criar `meta_ads.py` (wrapper + `health_check`).
- Criar `08_meta_check.py`.

**Dia 2** — Audiências
- `meta_audiences.py`: `sync_customers_audience()` (hash SHA-256 dos `whatsapp/email/nome` dos `top_clientes_por_ltv`).
- `meta_audiences.py`: `create_lookalike(seed_id, percent=1)` value-based.
- `meta_audiences.py`: `sync_website_retarget()` se pixel já está ativo.

**Dia 3** — Campanhas
- `meta_campaigns.py`: `boost_post()` cria Campaign + Adset + Ad em 1 transação.
- `meta_campaigns.py`: `create_retarget_campaign()`.
- `meta_campaigns.py`: `pause_campaign()` / `resume_campaign()`.

**Dia 4** — Insights
- `meta_insights.py`: `pull_daily_metrics()` itera campanhas, busca insights ontem.
- `09_meta_sync.py`: cron diário (sync audiência + pull métricas).
- Smoke test: criar campanha real com R$ 5/dia × 1 dia, validar dados no banco.

**Dia 5** — Blueprint Flask
- `meta_ads_panel.py`: todos os endpoints da seção 5.
- Registrar em `04_painel.py`.
- Mensagens de erro cliente-friendly.

**Dia 6** — UI aba "Tráfego"
- Tab + view + CSS seguindo padrão Vendas/Concorrentes.
- JS: `loadTrafegoDashboard()`, `loadAudiencias()`, `loadCampanhas()`, modal de boost.
- Estados "desconectado" e "conectado".

**Dia 7** — Polimento + competitor ads
- Adicionar `competitor_intel.coletar_ads_ativos(handle)` via Apify `facebook-ads-scraper`.
- UI da seção "Ads dos concorrentes".
- Testes manuais com Aline.

## 9. Verificação end-to-end (após Dia 7)

```bash
$ python 08_meta_check.py
# (deve passar tudo)

$ python 09_meta_sync.py
# Sync inicial: sobe N clientes pra Custom Audience.
# Pull de métricas: 0 campanhas → nada a fazer.

$ python run.py
# Abre painel em http://localhost:8000
```

**Roteiro manual da Aline:**

1. Aba **Tráfego** mostra status "Conectado · Ad Account ativo · Saldo R$ X".
2. Click "Sincronizar agora" em `haus_customers_v1` → toast "✓ N clientes enviados".
3. Aguarda Meta processar (~1h). Volta — tamanho aproximado aparece.
4. Click "Criar Lookalike 1%" → confirma → toast.
5. Aguarda Meta processar (~30min). Tamanho ~2M no BR.
6. Click "Impulsionar post" → cola URL do IG → R$ 30/dia × 7d → audience: Lookalike → confirma.
7. Campanha aparece em "Campanhas ativas" com status ATIVA.
8. Dia seguinte (após cron `09_meta_sync.py`): tabela mostra impressions, link_clicks, spend reais.
9. Aba **Insights** mostra cliques que entraram com `?utm_source=meta_ad` no grupo VIP.
10. Aba **Vendas** mostra eventuais vendas com `canal='meta_ad'` (Aline registra manualmente quando cliente comprar).

## 10. ROI medido (cruzamento entre módulos)

A grande virada que essa sprint habilita:

```
Sprint 1 já entrega:
  orders.valor_brl + orders.canal + orders.utm_source

Sprint 2 entrega:
  meta_ads_metrics.spend_brl + meta_ads_metrics.link_clicks

Cruzamento (query em insights.py ou nova view):
  ROAS = SUM(orders.valor_brl WHERE canal='meta_ad' OR utm_source='meta_ad')
       / SUM(meta_ads_metrics.spend_brl)
       no mesmo período
```

Esse número (ex: ROAS 4.2x) substitui qualquer métrica de impressão/CTR como prova de valor.

## 11. Custos operacionais

- **Meta Marketing API:** gratuita (rate limit generoso pra o volume da haus).
- **Apify `facebook-ads-scraper`** (sprint 7, opcional): ~$0.05 por handle, semanal × 5 concorrentes ≈ $0.20/mês.
- **OpenAI:** sem uso adicional na sprint.
- **Verba de ads** (orçamento da Aline, não build): R$ 300-500/mês inicial (10-15 R$/dia).
- **Verba de teste durante dev**: R$ 5/dia × 2 dias = R$ 10 (uma campanha real é necessária pra validar o pull de métricas no Dia 4).

## 12. O que NÃO entra nessa sprint (lista explícita)

- Conversion API server-side.
- Lookalike auto-refresh semanal (cron já existe, só não chama refresh — fica como toggle pra ativar depois).
- Multi-adset/split test.
- Catalog Ads (Advantage+) dinâmicos.
- Geração automática de criativos novos (hoje só impulsiona posts orgânicos existentes).
- Atribuição multi-touch (último-clique simples via UTM no MVP).
- Email/SMS pra prospects de Lookalike que ainda não compraram.

Todos os itens acima são candidatos a Sprint 3+ com base no que a Aline observar nos primeiros 30 dias rodando.

## 13. Sinergias com módulos existentes

| Módulo | Como Meta Ads usa |
|---|---|
| `prospects.valor_compra` + `orders.valor_brl` | Fonte da Custom Audience value-based (`top_clientes_por_ltv`). |
| `orders.utm_source` | Fechar ROAS real (não estimado). |
| `orders.canal` | Quando Aline marca venda `canal='meta_ad'`, conta como conversão atribuída. |
| `competitor_snapshots` + ads concorrentes (Apify) | Benchmark de criativos (qual copy/visual roda no nicho). |
| `product_media` (mode='ai_banner') | Criativos prontos pra Catalog Ads quando entrar (Sprint 3). |
| `links.cta_link()` (Sprint 1) | Já gera UTM correto pra cada canal — Meta usa `utm_source=meta_ad` quando criar campanha. |
| Aba "Acompanhar" | Aline marca prospects de Lookalike que respondem como ENTERED_GROUP/CUSTOMER. |

## 14. Critical files para implementação

- `c:\projetos\projeto-outbound-haus\database.py` — adicionar 3 tabelas + helpers (`save_audience`, `list_audiences`, `save_campaign`, `update_campaign_status`, `save_metrics_day`, etc).
- `c:\projetos\projeto-outbound-haus\meta_ads.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\meta_audiences.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\meta_campaigns.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\meta_insights.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\meta_ads_panel.py` ✨ novo (modelo: `competitor_panel.py`)
- `c:\projetos\projeto-outbound-haus\08_meta_check.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\09_meta_sync.py` ✨ novo
- `c:\projetos\projeto-outbound-haus\04_painel.py` — registrar blueprint, adicionar dependência ao `run.py`.
- `c:\projetos\projeto-outbound-haus\painel\index.html` — nova aba "Tráfego" + view + CSS + JS.
- `c:\projetos\projeto-outbound-haus\competitor_intel.py` — adicionar `coletar_ads_ativos(handle)` via Apify (Dia 7).
- `.env` — 8 variáveis META_* + 1 throttle.

## 15. Padrões a reutilizar (não reescrever)

- **Blueprint + throttle diário** — modelo canônico em `competitor_panel.py:21-36` (limite via env, log em tabela `*_runs`, endpoint `/uso-hoje`).
- **Cliente OpenAI lazy** — padrão `_get_client()` em `caption_gen.py:31-37` (aqui vira `_get_api()` pro facebook-business).
- **UTF-8 stdout no Windows** — `07_concorrente.py:9-15`. Replicar nos novos `08_meta_check.py` e `09_meta_sync.py`.
- **Estética do painel** — variáveis CSS + Fraunces italic + Outfit light + sage/gold accents. Aba "Vendas" recém-construída é template.
- **Modal pattern** — `#modal-overlay` + `#modal-title` + `#modal-body` + `fecharModal()` + `toast()`.
- **Migrations idempotentes** — `_run_migrations()` em `database.py:204-219` aceita ALTERs com erro de "já existe".
- **Mensagens cliente-friendly** — modelo `competitor_panel.py:115-135` (sem expor nome de tecnologia subjacente).

---

**Versão deste documento:** rascunho 1 · escrita antes do Dia 0. Atualizar a cada sprint review.
