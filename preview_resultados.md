# Preview: o que o agente vai retornar (simulado)

> Estes são exemplos do que o agente devolve em produção. Foram escritos
> seguindo as MESMAS regras do prompt, pra você visualizar a qualidade antes
> de gastar API key.

---

## Caso 1 — Carol Coracci (Maringá, arquiteta, mãe)

**Score:** 10/10 · **Confiança:** HIGH · **Status:** APROVAR

**Razões:**
- Confirmada em Maringá
- Arquiteta (perfil profissional alvo)
- Mãe, recebe família, mesa posta no estilo provençal
- Segue múltiplas lojas-alvo incluindo a haus

**Sinais:** localizacao_maringa, mae, recebe_casa, esteta, aspiracional, presenteadora

**Mensagem pronta:**
```
oi carol, vi sua mesa de domingo no estilo provençal e tinha tudo a 
ver com a gente — temos boxes que combinariam demais nesse universo. 
solto novidades primeiro no grupo VIP da haus, se quiser dar uma 
olhada: vip-haus.vercel.app

— Aline, da haus
```
*(280 caracteres)*

---

## Caso 2 — Isabela Mendes (Curitiba, arquiteta)

**Score:** 9/10 · **Confiança:** HIGH · **Status:** APROVAR

**Razões:**
- Curitiba confirmada
- Arquiteta com 12k seguidores (perfil aspiracional + cliente)
- Posta mesa posta no toile azul (estética idêntica à haus)
- Consome L'Occitane (marca compatível)

**Sinais:** localizacao_curitiba, recebe_casa, esteta, aspiracional

**Mensagem pronta:**
```
oi isabela, vi sua mesa no toile azul e fiquei encantada — é 
exatamente a estética da haus. solto novidades primeiro no grupo 
VIP, se quiser dar uma olhada: vip-haus.vercel.app

— Aline, da haus
```
*(220 caracteres)*

---

## Caso 3 — Jé (perfil cinza)

**Score:** 5/10 · **Confiança:** LOW · **Status:** REVISAR

**Razões:**
- Bio sem contexto (só frase religiosa)
- Localização não confirmada
- Posts genéricos sem mesa posta ou decoração
- Segue 2 lojas-alvo (sinal positivo fraco)

**Sinais:** aspiracional

**Mensagem:** `null` (aguarda decisão manual da Aline)

---

## Caso 4 — Decor Casa Atacado (conta comercial)

**Score:** 0/10 · **Confiança:** HIGH · **Status:** DESCARTAR

**Razões:**
- Conta de revenda/atacado (não consumidor final)
- Perfil concorrente
- Frequência de promoções e atacado evidente

**Sinais:** (nenhum)

**Mensagem:** `null`

---

## Caso 5 — Fernanda Aragão (SP, fora do raio)

**Score:** 5/10 · **Confiança:** HIGH · **Status:** REVISAR

**Razões:**
- Perfil ideal de cliente (mesa posta, presenteadora)
- MAS mora em São Paulo (fora do raio de entrega)
- Saudade do interior pode indicar disposição a pagar frete
- Decisão: Aline avalia se vale o frete pra SP

**Sinais:** mae, recebe_casa, esteta, aspiracional, presenteadora, localizacao_outras

**Mensagem:** `null` (Aline decide se manda ou descarta)

---

## Resumo dos 5 casos

| # | Perfil | Score | Status | Mensagem? |
|---|---|---|---|---|
| 1 | Carol (MGA, arquiteta) | 10 | APROVAR | ✅ pronta |
| 2 | Isabela (CWB, arquiteta) | 9 | APROVAR | ✅ pronta |
| 3 | Jé (cinza) | 5 | REVISAR | ⏸ manual |
| 4 | Atacado | 0 | DESCARTAR | ❌ |
| 5 | Fernanda (SP) | 5 | REVISAR | ⏸ manual |

## Observações do design

1. **Mensagens NUNCA são iguais.** Cada uma referencia um detalhe único
   do perfil (provençal, toile azul, etc).

2. **REVISAR é tão importante quanto APROVAR.** Captura os "talvez" sem
   queimar a conta com mensagens genéricas.

3. **DESCARTAR sem dó.** Conta comercial = não-cliente. Não tem segunda
   chance. Economiza DM da Aline.

4. **Casos limite (#5) viram REVISAR, não DESCARTAR.** Fernanda mora em SP
   mas tem perfil ideal. Decisão fica com a humana, não com a IA.
