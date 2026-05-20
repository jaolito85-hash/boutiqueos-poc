"""Agente WhatsApp da haus — integração Evolution API + UI no painel.

Camada de transport (evolution_client) isolada do resto, pra ser trocada por
Z-API quando o MVP virar produção. Tudo o mais (DB, webhook parser, API
endpoints, agente IA, UI) é provider-agnostic.
"""
