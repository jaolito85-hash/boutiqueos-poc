# haus tableware — identidade visual do template T1 (showcase de produto)

## Style Prompt

Editorial premium boutique. Paleta clara e cremosa (off-white quase pergaminho) com acentos azul royal profundo e cinza-pedra. Tipografia em alto contraste: serifa italic Fraunces para hero (nome do produto, "haus.") e sans Outfit fina e larga (letter-spacing alto, uppercase) para etiquetas, labels e CTAs. Movimento lento e suave (Ken Burns em fotos, fades de 0.6-1.2s, easings power3/power2). Sem brilho neon, sem gradientes saturados, sem qualquer estética "promocional" ou "festiva". O sentimento é de revista de decoração + boutique de bairro — discreto, atemporal, anfitriã.

## Colors

```
--bg:       #F7F4EE   (pergaminho/off-white — fundo principal)
--bg-card:  #FFFFFF   (cartão sobre fundo — para foto)
--ink:      #1F2426   (texto principal, alto contraste sobre bg claro)
--ink-soft: #4A4F52   (texto secundário)
--ink-muted:#888880   (legendas, microtexto)
--sage:     #5D6E55   (verde-sage profundo — coleção, etiquetas)
--gold:     #B8A876   (ouro velho — acento de linhas e detalhes)
--royal:    #2B3D7E   (azul royal — acento raro, em CTA e número de preço)
--line:     rgba(31,36,38,0.10)  (linhas finas decorativas)
```

Fundo SEMPRE em `--bg`. Texto em `--ink`. Preço pode receber `--royal` para destacar. Linhas decorativas em `--gold` (1-2px).

## Typography

```
display:  'Fraunces' italic 400-300, optical-sizing automático
ui:       'Outfit'   300-500, letter-spacing 0.15-0.25em em uppercase
```

Tamanhos no template 1080×1920:
- "haus." (brand): 140px Fraunces italic 300
- Nome do produto: 110px Fraunces italic 400
- Coleção: 28px Outfit 500 uppercase letter-spacing 0.25em
- Preço: 96px Fraunces italic 400, cor `--royal`
- CTA bottom: 24px Outfit 500 uppercase letter-spacing 0.2em
- Link: 28px Outfit 400 underline thin

## Motion rules

- Durations 0.6-1.2s para texto, 4-6s para Ken Burns
- Easings principais: `power3.out` (entradas), `power2.in` (saídas), `expo.out` (impacto sutil)
- Stagger 120-180ms entre elementos do mesmo grupo
- NUNCA bounce, elastic, back
- Ken Burns: scale 1.0 → 1.08 ao longo de 5s, linear

## What NOT to Do

1. NÃO usar emojis no vídeo (zero).
2. NÃO usar gradientes saturados ou cores néon. Paleta neutra apenas.
3. NÃO usar `bounce`, `elastic` ou easings "fofos". Tudo é refinado, controlado.
4. NÃO repetir o nome do produto em mais de 1 cena.
5. NÃO inserir texto sobre a foto do produto. Foto fica limpa; texto vive em cenas próprias.
6. NÃO usar `font-weight: bold`. Fraunces ressalta com italic + tamanho, não com peso.
