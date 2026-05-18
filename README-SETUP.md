# haus outbound — Sistema completo

Sistema de prospecção assistida por IA pra haus tableware.
Captura perfis do Instagram, qualifica com IA, e te entrega lista pronta
pra enviar DMs personalizadas.

## Arquitetura

```
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ 02_scraper.py   │ → │ 03_pipeline.py  │ → │ 04_painel.py    │
│ Apify           │   │ Agente IA       │   │ Painel da Aline │
│ ~640 perfis/sem │   │ qualifica       │   │ envia DMs       │
└─────────────────┘   └─────────────────┘   └─────────────────┘
       ↓                       ↓                       ↓
                       prospects.db (SQLite)
```

## Setup (uma vez só)

### 1. Instalar dependências

```powershell
pip install openai apify-client flask flask-cors
```

### 2. Configurar API keys

```powershell
# OpenAI (já configurada nos testes anteriores)
$env:OPENAI_API_KEY = "sk-proj-..."

# Apify (pegar em https://console.apify.com/account/integrations)
$env:APIFY_TOKEN = "apify_api_..."
```

Pra deixar permanente:
```powershell
[System.Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-proj-...', 'User')
[System.Environment]::SetEnvironmentVariable('APIFY_TOKEN', 'apify_api_...', 'User')
```

### 3. Inicializar banco

```powershell
python database.py
```

Deve criar `prospects.db` (SQLite) e mostrar stats zeradas.

## Uso semanal

### Segunda de manhã (você roda)

```powershell
cd C:\Projetos\projeto-outbound-haus

# 1. Captura perfis das 8 lojas-alvo (custa ~$0.20 Apify, ~5min)
python 02_scraper.py

# 2. Qualifica perfis novos com IA (custa ~$2.50 OpenAI, ~15min pra 500 perfis)
python 03_pipeline.py
```

### Durante a semana (Aline trabalha)

```powershell
# Liga o servidor (deixa rodando o dia todo)
python 04_painel.py
```

Aline acessa **http://localhost:8000** no navegador.

Trabalha **15 perfis por dia** (limite enforced pelo sistema).
Atalhos:
- **C** copia mensagem
- **E** marca como enviado
- **S** pula pro próximo
- **D** descarta como não-cliente

## Custos

| Item | Frequência | Custo |
|---|---|---|
| Apify (scraper) | 1x/semana | ~$0.20 = R$ 1 |
| OpenAI (qualificação) | 1x/semana, ~500 perfis | ~$2.50 = R$ 13 |
| Hospedagem | — | $0 (roda local) |
| **TOTAL** | — | **~R$ 14/semana, ~R$ 60/mês** |

## Protocolos de segurança (caminho A — conta pessoal)

Implementados no código pra proteger conta da Aline:

1. **Limite diário 15 DMs** — sistema bloqueia se tentar mais
2. **Painel mostra "Restam X hoje"** — Aline sempre sabe quanto falta
3. **Status por prospect** — NEW / READY / SENT / REPLIED (auditável)
4. **Tabela `events`** — registra tudo pra debug se algo der errado

## Estrutura de arquivos

```
projeto-outbound-haus/
├── database.py              ← Schema SQLite + helpers
├── lojas_alvo.json          ← Lista das 10 lojas-alvo (editável)
├── test_agente_openai.py    ← Agente IA (gpt-4.1-mini)
├── 02_scraper.py            ← Captura via Apify
├── 03_pipeline.py           ← Qualifica perfis NEW
├── 04_painel.py             ← Servidor Flask do painel
├── painel/
│   └── index.html           ← UI pra Aline
├── prospects.db             ← Banco SQLite (criado ao rodar)
├── agente-qualificador.md   ← Docs do prompt
├── CHANGELOG.md             ← Histórico de versões do prompt
└── README-SETUP.md          ← Este arquivo
```

## Próximos passos (quando o sistema rodar bem)

1. **Semana 1:** Validar com Aline. Calibrar prompt se necessário.
2. **Semana 2-4:** Operação normal. Acumular dados de resposta.
3. **Mês 2:** Migrar SQLite → Supabase. Adicionar dashboards de métricas.
4. **Mês 3:** Adicionar reativação automática (lead que não respondeu em 5 dias).

## Troubleshooting

**"APIFY_TOKEN não configurado"**: Configure conforme passo 2 do Setup.

**"Pipeline diz 'nada pra qualificar'"**: Rode `02_scraper.py` primeiro.

**"Painel não abre"**: Confirma `python 04_painel.py` está rodando. Olha porta 8000 livre.

**"Limite atingido"**: É proposital. Volte amanhã ou ajuste `DAILY_LIMIT` em `database.py` (não recomendo passar de 20).
