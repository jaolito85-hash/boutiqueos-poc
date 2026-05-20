# Agente WhatsApp da haus — Plano arquitetural

> Plano de arquitetura para implementar agente de IA no WhatsApp da haus tableware, integrado ao painel existente, com handoff humano e memória persistente. Aplica Karpathy guidelines (surface assumptions, simplicidade first, mudanças cirúrgicas, critérios verificáveis).

## Contexto

A haus tableware quer um agente de IA atendendo um número de WhatsApp dedicado: responder clientes 24/7, enviar fotos do catálogo, mandar link do grupo VIP, eventualmente link de pagamento — com handoff para a Aline assumir quando o agente não consegue. O atendimento ao vivo precisa aparecer no painel já existente (`painel/index.html`) numa interface estilo WhatsApp Web (lista de conversas à esquerda, thread à direita), com memória persistente por contato (lembra de conversas anteriores, perfil acumulado, vinculação opcional ao funil de prospects Instagram que já existe).

Hoje o sistema é Flask + SQLite local, painel monolítico (~5740 linhas), OpenAI usado em `caption_gen.py`/`competitor_intel.py`/`image_engine.py` (`gpt-4.1-mini` + lazy `_get_client()`), zero WebSocket, zero fila de jobs, zero APIs externas de plataforma — todas decisões registradas em memory_decisions. **WhatsApp justifica romper o "local-first"** (cliente manda msg às 22h domingo, notebook da Aline está desligado).

## Decisões já tomadas (pelo usuário)

| # | Decisão | Escolha | Implicação |
|---|---|---|---|
| 1 | Camada de envio | **Z-API** (BSP brasileiro, ~R$ 99/mês plano Negócios) | Setup em 1 dia, pt-BR, NF-e, sem Meta Business Manager |
| 2 | Hospedagem | **VPS Hetzner CX11** (~R$ 25/mês) | Painel sempre online, webhook nunca perde, acessível do celular |
| 3 | Número WhatsApp | **Novo dedicado** | Compra chip novo, sem conflito com app existente da loja |
| 4 | Horário do agente | **24/7** | IA sempre processa; Aline revisa de manhã e assume conforme |

## Assunções (defaults — corrigir antes da Fase 1 se diferente)

1. **Persona:** "Sou a IA da haus atendendo você agora" — explícita quando perguntado. Não personificar Aline.
2. **Tools no MVP:** 5 tools (buscar_produtos, enviar_foto_produto, enviar_link_grupo_vip, escalonar_humano, agendar_followup). Link de pagamento fica para v2 (Aline cola manual no handoff).
3. **Agente é reativo:** só responde quando cliente fala. Followup proativo é tool (Aline programa), não loop autônomo.
4. **Memória:** inline simples (últimas 20 msgs + rolling summary a cada 30 msgs + `perfil_json` acumulado no contato). Sem RAG/embeddings.
5. **Modelo:** `gpt-4.1-mini` (mesmo de `caption_gen.py`). Custo ~R$ 5/mês em 200 conversas.
6. **Áudio do cliente:** MVP transcreve "[áudio recebido]" e escalona automaticamente (não processa áudio na v1).
7. **Aline é única humana:** sem fila/atribuição multi-atendente.

## Arquivos críticos

### Backend (Python — todos novos exceto onde indicado)

- **Editar:** `04_painel.py` — registrar 3 novos blueprints
- **Editar:** `database.py` — adicionar 5 tabelas + migrations idempotentes (seguir padrão de `_run_migrations`)
- **Novo:** `wa/zapi_client.py` — wrapper REST do Z-API (send_text, send_image, send_audio, get_qr_status)
- **Novo:** `wa/webhook.py` — blueprint `/webhook/wa` (recebe msgs Z-API, persiste, dispatcha agente)
- **Novo:** `wa/api.py` — blueprint `/api/wa/*` (contacts, messages, send, stream SSE, handoff)
- **Novo:** `wa/agent.py` — loop conversacional com OpenAI tool calling
- **Novo:** `wa/agent_prompt.py` — system prompt + guardrails (referência: `agente-qualificador.md`)
- **Novo:** `wa/agent_tools.py` — 5 tools com JSON schemas
- **Novo:** `wa/scheduler.py` — APScheduler in-process para `wa_followups` (cron 1 min)

### Frontend

- **Editar:** `painel/index.html` — adicionar `'whatsapp'` ao `VALID_TABS` (linha 3525), nova view com layout 2-colunas, SSE client
- **Novo:** `painel/whatsapp.html` — fragment carregado via `fetch()` dentro da tab (split para não engordar mais o index.html monolítico — primeiro precedente neste projeto, mas justificado)

### Infra

- **Novo:** `deploy/Caddyfile` — reverse proxy HTTPS para o painel + webhook
- **Novo:** `deploy/haus-painel.service` — systemd unit
- **Novo:** `requirements.txt` — primeiro do projeto (deps: flask, flask-cors, openai, apify-client, werkzeug, requests, apscheduler)

## Utilitários existentes para reusar

| Função / Arquivo | Como usar |
|---|---|
| `_get_client()` em `caption_gen.py` | Copiar padrão de lazy-init OpenAI no `wa/agent.py` |
| `init_db()` + `_run_migrations()` em `database.py:374` | Adicionar tabelas wa_* dentro do mesmo arquivo, seguir padrão idempotente |
| Schema `sent_messages` em `database.py:262` | Padrão para `wa_messages` (qual msg, template_origem, foi_editada, outcome) |
| Catálogo: rotas `/media/raw/{pid}/*` e `/media/processed/{pid}/*` em `catalogo.py:372/380` | URLs já públicas — tool `enviar_foto_produto` usa direto |
| `setTab()` + hash URL + sessionStorage em `painel/index.html:3522` | Adicionar tab 'whatsapp' segue mesmo padrão |
| `escapeHtml()` em `painel/index.html:5158` | Sanitização de msgs do cliente antes de injetar no DOM |
| `toast()` em `painel/index.html:3440` | Feedback de "msg enviada", "handoff acionado" etc |
| `agente-qualificador.md` | Tom e estrutura de system prompt longo da haus |

## Schema novo (SQLite)

```sql
wa_contacts (
  id PK, phone TEXT UNIQUE NOT NULL,
  nome_exibicao TEXT,                         -- nome do WhatsApp
  prospect_id INT NULL FK prospects.id,       -- vincular IG↔WA (manual no MVP)
  perfil_json TEXT,                            -- {cidade, interesses[], ultima_intencao}
  mode TEXT CHECK(mode IN ('ai','human')) DEFAULT 'ai',
  status TEXT CHECK(status IN ('ativo','silenciado','bloqueado')) DEFAULT 'ativo',
  unread_count INT DEFAULT 0,
  primeira_msg_at, ultima_msg_at, created_at
)

wa_messages (
  id PK, contact_id INT FK wa_contacts.id,
  role TEXT,                                   -- user | assistant | tool | note
  content TEXT,
  media_url TEXT, media_type TEXT,             -- image | audio | document
  direction TEXT,                              -- in | out
  zapi_message_id TEXT UNIQUE,                 -- idempotência do webhook
  status TEXT,                                 -- sent | delivered | read | failed
  tool_name TEXT, tool_args_json TEXT,         -- quando role=tool
  created_at TIMESTAMP
)
INDEX (contact_id, created_at DESC)

wa_summaries (
  id PK, contact_id FK, summary TEXT,
  msgs_count_at_summary INT, created_at
)

wa_handoffs (
  id PK, contact_id FK, reason TEXT,
  requested_by TEXT,                           -- agent | human
  opened_at, closed_at
)

wa_followups (
  id PK, contact_id FK, run_at TIMESTAMP NOT NULL,
  texto TEXT, status TEXT DEFAULT 'pending',
  executed_at, created_by TEXT                 -- agent | human
)
INDEX (status, run_at)
```

**Decisão:** não criar `wa_conversations` — WhatsApp 1-to-1 não tem conceito de "conversa" separado do contato. Agrupar por `contact_id` basta.

## Fluxo da arquitetura

```text
                                                  ┌───────────────────────┐
Cliente WhatsApp                                  │   Painel (browser)    │
      │                                           │  tab "whatsapp"       │
      │ msg                                       │  ┌──┬───────────────┐ │
      ▼                                           │  │  │ thread        │ │
┌──────────────────┐  webhook POST /webhook/wa    │  │  │   msgs        │ │
│ Z-API instância  │ ──────────────────────────►  │  └──┴───────────────┘ │
│ api.z-api.io     │                              │     ▲   SSE push       │
└──────────────────┘                              └─────┼──────────────────┘
      ▲                                                  │
      │ POST /send-text /send-image                      │
      │                                                  │
┌─────┴──────────────────────────────────────────────────┴──────────┐
│         Flask :8000 (04_painel.py) na VPS Hetzner                 │
│         Caddy HTTPS reverse proxy → painel.dominio.com.br         │
│                                                                    │
│  wa/webhook.py ──► persiste wa_messages, atualiza wa_contacts     │
│         │           idempotência via zapi_message_id UNIQUE        │
│         ├─► if mode==ai  ──► wa/agent.py (thread)                 │
│         │                       │                                  │
│         │                       ├─ contexto: últimas 20 msgs       │
│         │                       │   + summary + perfil_json        │
│         │                       ├─ openai.chat.completions(        │
│         │                       │     model='gpt-4.1-mini',        │
│         │                       │     tools=[...], tool_choice='auto')│
│         │                       ├─ executa tool calls               │
│         │                       └─► zapi_client.send_text/image    │
│         │                                                          │
│         └─► if mode==human ──► só persiste, NÃO chama agente      │
│                                                                    │
│  wa/api.py ──► /api/wa/contacts, /api/wa/messages/:id,            │
│                /api/wa/send (Aline manual),                       │
│                /api/wa/stream (SSE realtime),                     │
│                /api/wa/handoff/:id (assume/release)               │
│                                                                    │
│  wa/scheduler.py ──► APScheduler tick 60s: wa_followups pendentes │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │ SQLite prospects.db  │
                       │ + wa_* tables        │
                       └──────────────────────┘
```

**Caminho handoff (agente decide):**
`webhook → agent → tool escalonar_humano → mode=human → SSE notify → painel toca som → Aline vê motivo → digita resposta → /api/wa/send → zapi_client → cliente`

**Caminho handoff (Aline decide):**
`Aline clica "Assumir" no header → POST /api/wa/handoff/:id {action:assume} → mode=human → daqui pra frente só humano`

## Tools do MVP (5)

Tools que entram no MVP, com JSON schemas pra OpenAI function calling:

1. **`buscar_produtos(categoria?, busca_texto?)`**
   - Lê `products` filtrado por `ativo=1`.
   - Retorna top 3 matches com nome, faixa_preco, colecao, descricao_breve, primeira imagem.
   - **NÃO retorna preço exato — só faixa.**

2. **`enviar_foto_produto(product_id, legenda)`**
   - Escolhe primeira `product_media` com `kind='processed'`, fallback `kind='raw'`.
   - Envia imagem + texto via Z-API.

3. **`enviar_link_grupo_vip(mensagem_curta)`**
   - Manda link `vip-haus.vercel.app` + texto curto.
   - CTA padrão registrado na memory.

4. **`escalonar_humano(motivo)`**
   - Cria `wa_handoffs`, muda `mode=human`.
   - Posta msg pro cliente: "Vou chamar a Aline, ela responde em instantes ❤️".
   - Manda push SSE pro painel; agente para de responder.

5. **`agendar_followup(quando_iso, texto, motivo)`**
   - Grava em `wa_followups`.
   - Cron leve dispara depois.

**Ficam pra v2:**

- `enviar_link_pagamento` — precisa Mercado Pago/PagBank. MVP: Aline cola link manual no handoff.
- `enviar_promocao_atual` — precisa tabela `promotions` + UI. MVP: agente fala "tenho novidades, te mando no grupo VIP".
- `criar_lead_no_funil` — vincular WA↔IG é manual.
- `consultar_estoque` — sem fonte de verdade hoje, não inventar.

## Guardrails

Tudo no system prompt + enforço via tools, não confiar só no LLM:

1. **Preço:** agente só fala pela tool `buscar_produtos`, que retorna `faixa_preco` (string vaga). Se cliente pedir valor exato → escalona.
2. **Sem estoque, sem prazo, sem desconto, sem reserva** — system prompt explícito + lista negra de palavras: se aparecer `garanto|tenho em estoque|amanhã chega|R$\s*\d+` na resposta gerada, bloqueia envio e escalona com `motivo='alucinação detectada'`.
3. **Fora do escopo da haus** (roupa, eletrônicos sem ser Le Creuset/Ariete) → escalona.
4. **Reclamação / problema com pedido** → escalona imediato, nunca tenta resolver.
5. **Limite de 5 mensagens consecutivas do agente sem msg do cliente** → escalona (defesa contra loop).

## UI da aba WhatsApp

Nova tab "whatsapp" no painel — layout em ASCII:

```text
┌──────────────────────────────────────────────────────────────┐
│ [tabs] outbound | review | ... | catalogo | concorrentes |WA │
├────────────────────┬─────────────────────────────────────────┤
│ Conversas          │  Cliente: Vanessa  📞 +55449...         │
│ [busca]            │  Modo: [🤖 IA] [👤 Assumir] [⏰ Mute]   │
│                    ├─────────────────────────────────────────┤
│ ● Vanessa  2min    │  [msg cliente]                          │
│   "tem do Toile?"  │     [msg agente IA]                     │
│   🤖 IA            │  [msg cliente com foto]                 │
│                    │     [foto+legenda do agente]            │
│ ○ Marina  1h       │                                         │
│   "vou pensar"     │                                         │
│   👤 Aline         ├─────────────────────────────────────────┤
│                    │ [✎ digite ou /produto /vip /escalonar ] │
│ ○ Camila  3h       │                              [Enviar]   │
└────────────────────┴─────────────────────────────────────────┘
```

Realtime via Server-Sent Events (`stream_with_context` do Flask, 1 endpoint `/api/wa/stream`). Fallback: polling 3s.

## Fases de implementação

### FASE 0 — Setup infra (~1 dia, fora do código)

1. Comprar chip novo (operadora local) — número dedicado.
2. Criar conta Z-API, plano Negócios (~R$ 99/mês), criar instância → escanear QR code com chip novo.
3. Criar VPS Hetzner CX11 Falkenstein ou Helsinki (€4,5/mês), Ubuntu 24.04.
4. Domínio: registrar (~R$ 40/ano) ou usar DuckDNS grátis.
5. Caddy + systemd + clonar repo na VPS. Variáveis no `.env`:
   - `OPENAI_API_KEY`
   - `ZAPI_INSTANCE_ID`, `ZAPI_TOKEN`, `ZAPI_CLIENT_TOKEN`
   - `ZAPI_WEBHOOK_SECRET` (validar HMAC)
   - `HAUS_VPS_DOMAIN`

**Critério verificável:** `curl https://painel.dominio.com.br/` retorna o HTML do painel; `curl POST /webhook/wa` aceita JSON do Z-API.

### FASE 1 — Backend mínimo, sem IA (~1-2 dias)

1. Adicionar 5 tabelas em `database.py` + migrations.
2. Criar `wa/zapi_client.py`: 3 funções (`send_text`, `send_image_url`, `get_media_url`). Lê env vars. Lib `requests`.
3. Criar `wa/webhook.py`:
   - `POST /webhook/wa` — valida HMAC, persiste msg em `wa_messages`, upsert `wa_contacts`, incrementa `unread_count`. Retorna 200 em <100ms.
   - Idempotência: tenta INSERT em `wa_messages.zapi_message_id`, ignora UNIQUE violation.
4. Criar `wa/api.py`:
   - `GET /api/wa/contacts?status=ativo`
   - `GET /api/wa/messages/<contact_id>?limit=50`
   - `POST /api/wa/send` — `{contact_id, content, media_url?}`
   - `POST /api/wa/handoff/<contact_id>` — `{action: 'assume'|'release', reason?}`
5. Registrar blueprints em `04_painel.py`.

**Critério verificável:**

- Mando msg do meu WhatsApp pessoal → aparece em `wa_messages` em <2s.
- `curl POST /api/wa/send {contact_id:1, content:'oi'}` → msg chega no meu WhatsApp em <3s.
- 3 msgs idênticas (mesmo `zapi_message_id`) → só 1 linha em `wa_messages` (idempotência).

**Risco:** payload Z-API pode ter campos não documentados; validar manualmente uma msg antes de fechar parser. Logar payload bruto em `wa_messages.raw_json` opcional pra debug.

### FASE 2 — UI WhatsApp no painel (~2-3 dias)

1. Adicionar `'whatsapp'` ao `VALID_TABS` em `painel/index.html:3525`.
2. Criar `painel/whatsapp.html` (fragment) — primeira vez que split é usado neste projeto.
3. Loader em `setTab('whatsapp')`: `fetch('/painel/whatsapp.html')` na primeira vez, cachear em closure.
4. SSE: `GET /api/wa/stream` (Flask `stream_with_context` + `Response(mimetype='text/event-stream')`). Eventos: `new_message`, `mode_changed`, `contact_updated`. Cliente em JS faz `EventSource`.
5. Slash commands no input:
   - `/produto <busca>` → POST envia foto+nome+faixa_preco do match.
   - `/vip` → envia link `vip-haus.vercel.app`.
   - `/assumir` `/devolver` — atalho dos botões.
6. Indicadores realtime:
   - Card brilha quando msg chega e thread não está aberta.
   - Som curto (HTML `<audio>` com `data:audio/...` base64).
   - Título do navegador pisca `(3) haus painel` quando há unread.
   - Badge dourada no tab "WhatsApp" no header (reusa estilo `.tab-badge`).

**Critério verificável:**

- Aline mantém conversa-teste completa pelo painel sem abrir WhatsApp Web.
- Msg nova do cliente aparece no painel em <3s sem refresh.
- F5 mantém conversa aberta (deep-link `#whatsapp/<contact_id>`).

**Risco:** SSE atrás de Caddy precisa configuração específica (sem buffer). Testar antes. Fallback: polling 3s em `/api/wa/poll?since=<timestamp>`.

### FASE 3 — Agente IA com tool calling (~3-4 dias)

1. Criar `wa/agent_prompt.py` — system prompt longo (200-400 linhas):
   - Persona ("Sou a IA da haus...")
   - Regras de produto (não inventar preço/estoque/prazo)
   - Quando escalar (fora do escopo, reclamação, pedido de preço exato)
   - Estilo (curto, caloroso, emoji moderado, pt-BR Maringá)
   - Lista negra de afirmações (palavras-gatilho que bloqueiam envio)
2. Criar `wa/agent_tools.py` — 5 tools com JSON schema OpenAI.
3. Criar `wa/agent.py`:
   - `process_incoming(contact_id, msg_id)` (chamado pelo webhook se `mode==ai`).
   - Carrega: últimas 20 msgs + último `wa_summaries.summary` + `wa_contacts.perfil_json`.
   - Monta `messages = [{role:'system', content:prompt+summary+perfil}, ...historico]`.
   - `client.chat.completions.create(model='gpt-4.1-mini', messages=..., tools=TOOLS, tool_choice='auto')`.
   - Loop: enquanto resposta tem `tool_calls`, executa, append, chama de novo (max 5 iterações).
   - Resposta final: `zapi_client.send_text`, persiste em `wa_messages` com `role='assistant'`.
4. Auto-summary: trigger quando contato tem >30 msgs sem summary recente. Chamada separada gpt-4.1-mini, grava em `wa_summaries` + atualiza `wa_contacts.perfil_json`.

**Critério verificável:**

- 10 conversas-teste com diferentes intenções (preço, foto produto, horário, reclamação, fora do escopo). Revisão manual: ≥8/10 respostas aceitáveis.
- "Quanto custa o prato Toile?" → agente busca catálogo, fala faixa, oferece foto, OU escalona se cliente insistir em preço exato.
- "Vocês entregam amanhã?" → escalona (prazo não é responsabilidade do agente).
- "Quero falar com alguém" → escalona imediato.
- Após 35 msgs num contato, `wa_summaries` tem entry; próxima resposta agente referencia ("Você comentou semana passada sobre o jogo Toile...").

**Risco:** alucinação. Mitigação:

- Validação pós-geração: regex de lista negra → se match, bloqueia envio, escalona automático.
- Logar TUDO (input prompt + raw response + tool calls + decisão) em `wa_messages` com `role='tool'` para auditoria.

### FASE 4 — Handoff completo + indicadores (~1-2 dias)

1. Tool `escalonar_humano` ativa: cria `wa_handoffs`, muda mode, posta msg para cliente, SSE notifica.
2. Botões no header:
   - "Assumir" (quando mode=ai) → `POST /api/wa/handoff {action:'assume'}`.
   - "Devolver à IA" (quando mode=human) → `{action:'release'}`. Fecha `wa_handoffs.closed_at`.
3. Card de conversa:
   - Badge "🤖 IA" verde sage quando mode=ai.
   - Badge "👤 Aline" gold quando mode=human.
   - Badge vermelha "⚠ Escalado" se handoff aberto há >30 min sem Aline assumir.
4. Som + título piscando: 1 `<audio>` global + `setInterval` no título quando `document.hidden===true`.

**Critério verificável:**

- "Qual o preço exato do prato Toile?" → agente escalona em 1 turno → Aline ouve som → assume → responde → devolve → próxima msg agente já responde.
- Notificação funciona em browser minimizado (Chrome + Edge).

### FASE 5 — Followups + qualidade + operação (~2-3 dias)

1. `wa/scheduler.py`: APScheduler `BackgroundScheduler`, tick 60s. Lê `wa_followups WHERE status='pending' AND run_at<=now`, dispara `zapi_client.send_text`, marca `status='executed'`.
2. Aline cria followup pela UI: input no header da thread "📌 Lembrar de mandar [texto] em [datetime]".
3. Tela de auditoria: rota `/api/wa/audit` lista últimas 50 respostas do agente com botão "Marcar como ruim" (incrementa `wa_messages.audit_score`).
4. Coluna manual em `wa_contacts`: input "Vincular ao prospect IG" no header da thread.
5. README `wa/README.md`:
   - Env vars
   - Como ver logs (`journalctl -u haus-painel -f`)
   - Como pausar agente globalmente (toggle env var)
   - Como reescanear QR code do Z-API
   - Backup do SQLite (`sqlite3 prospects.db .backup`)

**Critério verificável:** 1 semana com Aline em produção. Métricas mínimas:

- ≥80% conversas resolvidas sem handoff.
- 0 alucinações de preço/estoque/prazo (medido pela auditoria).
- ≤2 min de downtime médio por dia.
- Aline reporta NPS ≥7/10.

## Custo mensal estimado

| Item | Volume base | Volume 10x |
|---|---|---|
| Z-API plano Negócios | R$ 99 | R$ 99 |
| VPS Hetzner CX11 | R$ 25 | R$ 25 |
| OpenAI gpt-4.1-mini | R$ 5 (200 conv) | R$ 50 (2000 conv) |
| Domínio (rateio anual) | R$ 3 | R$ 3 |
| **Total** | **~R$ 132/mês** | **~R$ 177/mês** |

## Verificação end-to-end final

Antes de promover pra "produção":

1. **Smoke completo:** mensagem do meu celular pessoal → painel mostra → agente responde com foto do catálogo → Aline assume → responde → devolve → segunda mensagem o agente responde.
2. **Resiliência:** desligo a VPS por 5 min, ligo de volta. Mensagens durante downtime devem chegar (Z-API tem retry).
3. **Carga leve:** 20 conversas simuladas em paralelo (script Python loop). Painel não trava, agente responde todas em <15s média.
4. **Segurança:**
   - Webhook Z-API rejeita request sem HMAC válido.
   - `/api/wa/*` endpoints atrás de auth básica (htpasswd Caddy) — painel não é público.
   - `.env` no `.gitignore`, secrets nunca em commit.
   - LGPD: README explica que mensagens de clientes ficam no SQLite da VPS; backup em local controlado por Aline.
5. **Guardrails:** 10 "stress tests" tentando enganar o agente (jailbreak, pedido de desconto, pergunta sobre concorrente). Nenhuma deve resultar em afirmação proibida.

## Riscos consolidados / NÃO fazer

- **Não** colocar painel publicamente sem auth (htpasswd Caddy bloqueia).
- **Não** salvar áudio de cliente em disco (processar com Whisper na v2 — MVP escalona).
- **Não** integrar pagamento real no MVP (link cola manual no handoff).
- **Não** auto-iniciar conversas (agente é reativo + followups programados pela Aline).
- **Não** suportar grupos do WhatsApp no MVP (só 1-to-1).
- **Não** usar mesma instância Z-API com WhatsApp Web aberto no celular (Z-API "captura" o número; Aline acessa só pelo painel).
- **Não** crescer painel/index.html além das 5740 linhas atuais — fragmento separado `painel/whatsapp.html` é a partir daqui.
- **Não** introduzir framework JS (React/Vue) — vanilla continua suficiente.

## Sequência de aprovação sugerida

1. **Fase 0** primeiro (chip + Z-API + VPS) — você pode tocar essa parte em paralelo enquanto eu codifico Fase 1.
2. **Fase 1** (backend mínimo) + smoke test isolado.
3. **Fase 2** (UI manual sem IA) → você usa por 1-2 dias como WhatsApp Web da loja.
4. **Fase 3** (IA com tools) → conversas-teste extensivas antes de soltar pra clientes reais.
5. **Fase 4** (handoff polido) → 3-5 dias de uso real com Aline.
6. **Fase 5** (followups + auditoria) → finaliza o MVP.

Total estimado: **~10-12 dias de desenvolvimento ativo** + setup Fase 0 paralelo.
