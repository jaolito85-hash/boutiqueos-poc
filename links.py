"""
links.py — Helpers de link com UTM tracking pra fechar atribuição de canais.

Toda chamada-para-ação que sai pro mundo (caption Instagram, mensagem WhatsApp,
overlay de vídeo, mensagem de DM gerada por CSV) passa pelo `cta_link(canal)` —
assim, quando a Aline registra uma venda, o painel sabe de qual canal veio.

Convenção dos canais (use snake_case curto):
    dm_outbound        DM Instagram/TikTok enviada pela Aline (fila Outbound)
    dm_outbound_csv    DM saída de leads importados via CSV
    feed_organico      Caption de post no feed do Instagram
    feed_retrato       Caption específica de feed retrato
    story              Sticker/sobreposição em Story do Instagram
    tiktok             Caption no TikTok
    whatsapp_vip       Mensagem postada no grupo VIP do WhatsApp
    reel_video         Overlay em reel/vídeo gerado pelo video_engine
    readme_pacote      README do .zip de postagem (packager)
    meta_ad            Tráfego pago Meta (sprint 2)
    organico_outro     Genérico, quando não há canal específico

`campanha` é opcional — quando informada, fica em utm_campaign (ex:
'colecao_outono_2026', 'box_dia_das_maes').
"""

from __future__ import annotations

GRUPO_VIP_URL = "vip-haus.vercel.app"


def cta_link(canal: str, campanha: str | None = None) -> str:
    """Retorna o link do grupo VIP com utm_source (e opcionalmente utm_campaign).

    Exemplos:
        cta_link("dm_outbound")
        # -> vip-haus.vercel.app/?utm_source=dm_outbound

        cta_link("whatsapp_vip", "colecao_outono_2026")
        # -> vip-haus.vercel.app/?utm_source=whatsapp_vip&utm_campaign=colecao_outono_2026
    """
    canal_safe = (canal or "organico_outro").strip().replace(" ", "_").lower()
    qs = f"utm_source={canal_safe}"
    if campanha:
        camp_safe = campanha.strip().replace(" ", "_").lower()
        qs += f"&utm_campaign={camp_safe}"
    return f"{GRUPO_VIP_URL}/?{qs}"
