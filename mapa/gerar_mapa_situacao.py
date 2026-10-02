#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gerar_mapa_situacao.py
=======================

Gera automaticamente o "Mapa de Situação" (croqui) de um empreendimento de
bovinocultura a partir do GPKG estadual do Paraná, para instruir os processos
da GP Consultoria Ambiental (DLAM, LAC, LAS, MCE etc.).

-----------------------------------------------------------------------------
VERSÃO 3 — conformidade IN IAT nº 23/2026
-----------------------------------------------------------------------------
Novidades em relação à v2.1:

  1. Coordenadas UTM (E, N) do empreendimento no cartucho.
  2. Rodapé: "Sistema de Coordenadas:" (era "...Geográficas:", incorreto para
     um sistema projetado).
  3. Limites da propriedade: somente base CAR (a matrícula foi descartada por
     decisão do Guilherme).
  4. Área construída e área livre no cartucho, vindas da anamnese.
  5. Ponto da estrutura física (anamnese), com o rótulo "Estrutura física"
     desenhado ACIMA do ponto.
  6. Cota de distância entre a estrutura física e o corpo hídrico mais
     próximo — atende a alínea "c" da lista do mapa de situação, que pede
     "distância dos corpos hídricos", não apenas os corpos hídricos.
  7. Faixa de APP: buffer sobre a hidrografia conforme Lei 12.651/2012.
  8. Remanescente vegetal do SICAR: desenhado no mapa, com a área DENTRO do
     imóvel calculada e levada ao cartucho e à legenda.
  9. Rótulos com nome real: vias (`name`), hidrografia (`noriocomp`) e
     pontos de referência (`localidades_pontos_osm`.`name`).

GEOMETRIA DA PRANCHA
Continua espelhando o modelo_mapa_situacaof.qpt que você validou à mão no
Compositor. Duas caixas precisaram mudar para o conteúdo novo caber — as
duas estão marcadas com "ALTERADO v3" no bloco GEOMETRIA, e nada além delas
foi tocado:
  * Caixa de identificação: 34,68 mm -> 44,00 mm (o cartucho ganhou 4 linhas).
  * Bloco LOCALIZAÇÃO: desceu 9,32 mm e encolheu na mesma medida, devolvendo
    o espaço que o cartucho tomou. Continua terminando em 124,08 mm.
  * Legenda: subiu 1,8 mm e virou DUAS COLUNAS (eram 3 entradas, agora são 7).
    Termina em 173,107 mm, encostando no rodapé sem invadi-lo.

Uso típico (dentro do container qgis/qgis, que já tem PyQGIS instalado):

    python3 gerar_mapa_situacao.py \
        --gpkg /caminho/para/dados_pr.gpkg \
        --cod-imovel "PR-4100103-1C53FE5BDF6548E7B3FB569614FB7790" \
        --lat -24.247617 --lon -51.673683 \
        --estrutura-lat -24.248100 --estrutura-lon -51.674200 \
        --cliente "CHT GROUP INSUMOS AGRICOLAS LTDA" \
        --doc-cliente "42.101.140/0002-87" \
        --endereco "Rua João Maria Stresser, 270 - Centro - Ivaiporã/PR" \
        --area-ha 12.9659 \
        --area-construida-ha 0.4200 \
        --area-livre-ha 12.5459 \
        --vegetacao /caminho/para/vegetacao_sicar.shp \
        --logo /caminho/para/logo_gp_consultoria.png \
        --logo-fonte /caminho/para/logo_iat.png \
        --rosa-ventos /caminho/para/rosa_dos_ventos.svg \
        --saida /caminho/para/mapa_situacao.pdf

ATENÇÃO ÀS UNIDADES: --area-ha, --area-construida-ha e --area-livre-ha são
todas em HECTARES, para ficarem coerentes entre si no cartucho. Se a anamnese
devolver a área construída em m², divida por 10.000 antes de passar.
"""

import argparse
import re
import sys
import textwrap
import time
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsProject,
    QgsField,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemLabel,
    QgsLayoutItemShape,
    QgsLayoutSize,
    QgsLayoutExporter,
    QgsUnitTypes,
    QgsSingleSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsScaleBarSettings,
    QgsLayoutMeasurement,
    QgsReadWriteContext,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
)
from qgis.core import Qgis
from qgis.PyQt.QtCore import QRectF, Qt, QCoreApplication
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtXml import QDomDocument


# ---------------------------------------------------------------------------
# COMPATIBILIDADE QGIS 3.x / 4.x
#
# O QGIS 4 roda em Qt6/PyQt6, que só aceita constantes com o nome completo
# (Qt.AlignmentFlag.AlignLeft, não Qt.AlignLeft), e removeu vários apelidos
# antigos (QgsUnitTypes.LayoutMillimeters virou Qgis.LayoutUnit.Millimeters,
# etc.). Cada constante abaixo tenta o nome novo primeiro e cai para o antigo,
# então o mesmo arquivo roda no QGIS 3.34 e no 4.x.
# ---------------------------------------------------------------------------

def _enum(*caminhos):
    for caminho in caminhos:
        try:
            return eval(caminho, globals())
        except (AttributeError, NameError, ImportError):
            continue
    raise AttributeError("Nenhuma das constantes existe nesta versão do QGIS: "
                         + ", ".join(caminhos))


try:
    from qgis.core import QgsLabeling
except ImportError:
    QgsLabeling = None

_G = "QgsLayoutItemMapGrid"
_EXPORT_OK = _enum("QgsLayoutExporter.ExportResult.Success", "QgsLayoutExporter.Success")

_GRADE_BORDA_ESQ = _enum("Qgis.MapGridBorderSide.Left", f"{_G}.BorderSide.Left", f"{_G}.Left")
_GRADE_BORDA_DIR = _enum("Qgis.MapGridBorderSide.Right", f"{_G}.BorderSide.Right", f"{_G}.Right")
_GRADE_BORDA_TOPO = _enum("Qgis.MapGridBorderSide.Top", f"{_G}.BorderSide.Top", f"{_G}.Top")
_GRADE_BORDA_BAIXO = _enum("Qgis.MapGridBorderSide.Bottom", f"{_G}.BorderSide.Bottom", f"{_G}.Bottom")
_GRADE_DIR_HORIZONTAL = _enum("Qgis.MapGridAnnotationDirection.Horizontal",
                              f"{_G}.AnnotationDirection.Horizontal", f"{_G}.Horizontal")
_GRADE_DIR_VERTICAL = _enum("Qgis.MapGridAnnotationDirection.Vertical",
                            f"{_G}.AnnotationDirection.Vertical", f"{_G}.Vertical")
_GRADE_FMT_DECIMAL_SUFIXO = _enum("Qgis.MapGridAnnotationFormat.DecimalWithSuffix",
                                  f"{_G}.AnnotationFormat.DecimalWithSuffix", f"{_G}.DecimalWithSuffix")
_GRADE_SO_MOLDURA = _enum("Qgis.MapGridStyle.FrameAndAnnotationsOnly", "Qgis.MapGridStyle.FrameAnnotationsOnly",
                          f"{_G}.GridStyle.FrameAnnotationsOnly", f"{_G}.FrameAnnotationsOnly")
_GRADE_LADO_FLAG_ESQ = _enum("Qgis.MapGridFrameSideFlag.Left", f"{_G}.FrameSideFlag.FrameLeft", f"{_G}.FrameLeft")
_GRADE_LADO_FLAG_DIR = _enum("Qgis.MapGridFrameSideFlag.Right", f"{_G}.FrameSideFlag.FrameRight", f"{_G}.FrameRight")
_GRADE_LADO_FLAG_TOPO = _enum("Qgis.MapGridFrameSideFlag.Top", f"{_G}.FrameSideFlag.FrameTop", f"{_G}.FrameTop")
_GRADE_LADO_FLAG_BAIXO = _enum("Qgis.MapGridFrameSideFlag.Bottom", f"{_G}.FrameSideFlag.FrameBottom", f"{_G}.FrameBottom")
_GRADE_MOLDURA_TICKS = _enum("Qgis.MapGridFrameStyle.InteriorTicks", f"{_G}.FrameStyle.InteriorTicks", f"{_G}.InteriorTicks")
_GRADE_FORA_DO_MAPA = _enum("Qgis.MapGridAnnotationPosition.OutsideMapFrame",
                            f"{_G}.AnnotationPosition.OutsideMapFrame", f"{_G}.OutsideMapFrame")
_GRADE_MOSTRAR_TUDO = _enum("Qgis.MapGridComponentVisibility.ShowAll", f"{_G}.DisplayMode.ShowAll", f"{_G}.ShowAll")

_FIGURA_ZOOM = _enum("QgsLayoutItemPicture.ResizeMode.Zoom", "QgsLayoutItemPicture.Zoom")
_FORMA_ELIPSE = _enum("QgsLayoutItemShape.Shape.Ellipse", "QgsLayoutItemShape.Ellipse")
_FORMA_RETANGULO = _enum("QgsLayoutItemShape.Shape.Rectangle", "QgsLayoutItemShape.Rectangle")

_LBL_LINHA = _enum("Qgis.LabelPlacement.Line", "QgsPalLayerSettings.Placement.Line", "QgsPalLayerSettings.Line")
_LBL_SOBRE_PONTO = _enum("Qgis.LabelPlacement.OverPoint", "QgsPalLayerSettings.Placement.OverPoint",
                         "QgsPalLayerSettings.OverPoint")
_LBL_QUADRANTE_ACIMA = _enum("Qgis.LabelQuadrantPosition.Above",
                             "QgsPalLayerSettings.QuadrantPosition.QuadrantAbove",
                             "QgsPalLayerSettings.QuadrantAbove")
_LBL_ACIMA_DA_LINHA = _enum("Qgis.LabelLinePlacementFlag.AboveLine",
                            "QgsLabeling.LinePlacementFlag.AboveLine", "QgsPalLayerSettings.AboveLine")
_LBL_ORIENTACAO_MAPA = _enum("Qgis.LabelLinePlacementFlag.MapOrientation",
                             "QgsLabeling.LinePlacementFlag.MapOrientation",
                             "QgsPalLayerSettings.MapOrientation")

_BARRA_ALINHA_ESQ = _enum("QgsScaleBarSettings.Alignment.AlignLeft", "QgsScaleBarSettings.AlignLeft")
_BARRA_SEGMENTO_FIXO = _enum("Qgis.ScaleBarSegmentSizeMode.Fixed",
                             "QgsScaleBarSettings.SegmentSizeMode.SegmentSizeFixed",
                             "QgsScaleBarSettings.SegmentSizeFixed")

_UN_METROS = _enum("Qgis.DistanceUnit.Meters", "QgsUnitTypes.DistanceMeters")
_UN_LAYOUT_MM = _enum("Qgis.LayoutUnit.Millimeters", "QgsUnitTypes.LayoutMillimeters")
_UN_RENDER_MM = _enum("Qgis.RenderUnit.Millimeters", "QgsUnitTypes.RenderMillimeters")
_UN_RENDER_PT = _enum("Qgis.RenderUnit.Points", "QgsUnitTypes.RenderPoints")

# Tipo de campo texto: QGIS >= 3.38 usa QMetaType; antes, QVariant.
try:
    from qgis.PyQt.QtCore import QMetaType
    _TIPO_TEXTO = QMetaType.Type.QString
    if Qgis.QGIS_VERSION_INT < 33800:
        raise ImportError
except (ImportError, AttributeError):
    from qgis.PyQt.QtCore import QVariant
    _TIPO_TEXTO = QVariant.String

# Teto de feições copiadas por camada de contexto numa única geração. As
# camadas de origem têm centenas de milhares a milhões de feições estaduais;
# sem teto, uma consulta espacial mal-indexada pode copiar dezenas de milhares
# de feições em Python e estourar a memória.
LIMITE_FEICOES_CONTEXTO = 3000

VERSAO = "3.14 (compatível com QGIS 3.34+ e 4.x; área livre calculada; município da malha IAT)"


# ---------------------------------------------------------------------------
# CONFIG — nomes de camada e campo do GPKG estadual (dados_pr).
# ---------------------------------------------------------------------------

FIELD_COD_IMOVEL = "cod_imovel"
FIELD_AREA_HA = "num_area"
# Rótulos por camada: o script tenta os campos NA ORDEM e usa o primeiro que
# estiver preenchido. Se nenhum tiver conteúdo, aplica o texto padrão.
#
#  * Vias: `name` primeiro; sem nome, cai em `ref` (PR-092, BR-153...). Sem os
#    dois, fica sem rótulo — estrada vicinal sem identificação não ganha texto
#    inventado.
#  * Hidrografia: `noriocomp` é o campo da base da ANA; `name` entra como
#    alternativa caso a camada usada seja de outra origem. Sem nenhum dos dois,
#    recebe "Córrego sem denominação" — a IN pede os corpos hídricos
#    identificados, e deixar a linha muda sugere que ela não foi avaliada.
FIELD_VIA_NOMES = ["name", "ref"]
VIA_ROTULO_PADRAO = ""

FIELD_HIDRO_NOMES = ["noriocomp", "name"]
HIDRO_ROTULO_PADRAO = "Córrego sem denominação"

FIELD_LOCALIDADE_NOMES = ["name"]
LOCALIDADE_ROTULO_PADRAO = ""
FIELD_MUNICIPIO_NOME = "nome"  # AJUSTAR se o nome real divergir

# ---------------------------------------------------------------------------
# ONDE CADA CAMADA MORA
#
# A v3.0 assumia que todas as camadas estavam dentro de um único GPKG
# (`--gpkg dados_pr.gpkg|layername=...`). Na pasta real elas estão em ARQUIVOS
# SEPARADOS, um .gpkg por camada, com nomes que nem sempre batem com o nome da
# camada lá dentro. Daí esta tabela: para cada camada lógica, os nomes de
# arquivo que o script procura dentro de --dados-dir, em ordem de preferência.
#
# A resolução de cada camada segue esta ordem:
#   1) o caminho explícito passado no argumento correspondente
#      (--camada-car, --camada-vias, ...), que aceita '.gpkg|layername=x';
#   2) o primeiro arquivo desta lista que existir em --dados-dir;
#   3) o --gpkg combinado, como 'arquivo.gpkg|layername=<nome lógico>'.
#
# Se o arquivo tiver mais de uma camada dentro, o script lista as opções e
# escolhe a que melhor casa com o nome lógico — não é preciso saber de cor o
# nome interno da camada.
# ---------------------------------------------------------------------------

FONTES_CANDIDATAS = {
    "car": ["imoveis_pr_poligonos_car.gpkg"],
    "vias": ["vias_linhas_OpenStreetMap.gpkg", "vias_linhas_osm.gpkg"],
    "hidrografia": ["hidrografia_linhas_ANA.gpkg"],
    "localidades": ["localidades_pontos.gpkg", "localidades_pontos_osm.gpkg"],
    "municipios": ["municipios_poligonos_iat.gpkg"],
    "vegetacao": ["sicar_vegetacao_nativa.gpkg"],
}

# Nome lógico usado como `layername` quando a camada vem do GPKG combinado,
# e como pista para escolher a sublayer certa dentro de um arquivo.
NOMES_LOGICOS = {
    "car": "imoveis_pr_poligonos_car",
    "vias": "vias_linhas_osm",
    "hidrografia": "hidrografia_linhas_ANA",
    "localidades": "localidades_pontos_osm",
    "municipios": "municipios_poligonos_iat",
    "vegetacao": "vegetacao",
}

# Raio (metros) usado para: (1) buffer do fallback ponto+lat/long,
# (2) recorte das camadas de contexto ao redor do imóvel.
RAIO_CONTEXTO_M = 1500

# ENQUADRAMENTO do mapa principal: o quadro passa a ser ditado pelo IMÓVEL mais
# esta margem (m) de cada lado, e não por um raio fixo de contexto. Com 150 m o
# croqui de uma propriedade típica sai perto de 1:6.000, contra os 1:15.385
# anteriores. Resolve sozinho boa parte da poluição visual — entra menos
# território no quadro — e é o que torna legível o anel de 50 m do Art. 68, que
# a 1:15.385 media 3,25 mm no papel.
MARGEM_ENQUADRAMENTO_M = 400.0
LADO_MINIMO_M = 600.0

# ESCALAS REDONDAS — o mapa nunca sai em 1:5.897. O script calcula o quadro
# necessário (imóvel + margem), converte em escala, sobe para o próximo degrau
# desta escada e recalcula o quadro a partir dele. O resultado é sempre uma
# escala cheia, como se espera de uma peça técnica, e o imóvel nunca encosta na
# borda porque o arredondamento é sempre PARA CIMA (afasta, nunca aproxima).
#
# A escada é densa de propósito: entre 4.000 e 10.000 os degraus são de 1.000,
# então o excesso de enquadramento fica em no máximo ~12%. Uma escada rala
# (só 5.000 / 10.000 / 25.000) jogaria um imóvel de 1:5.200 direto para
# 1:10.000 e desfaria o zoom.
ESCALAS_REDONDAS = [
    500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000,
    5500, 6000, 7000, 8000, 9000, 10000, 11000, 12500, 15000,
    20000, 25000, 30000, 40000, 50000,
]

# Largura útil do mapa em metros de papel (195 mm), usada para converter
# lado do quadro <-> escala.
MAPA_W_M = 0.195


def escala_redonda(lado_m):
    """
    Converte a largura do quadro (m) na próxima escala redonda e devolve
    (escala, novo_lado_m). Acima do último degrau, mantém a escala calculada.
    """
    bruta = lado_m / MAPA_W_M
    for degrau in ESCALAS_REDONDAS:
        if degrau >= bruta - 0.5:
            return degrau, degrau * MAPA_W_M
    return bruta, lado_m

# RECORTE AMBIENTAL: APP e remanescente vegetal são desenhados apenas dentro do
# imóvel mais esta faixa (m). A norma pede a APP e a vegetação DO IMÓVEL; pintar
# o quadro inteiro fazia o contexto pesar mais que o objeto do documento — na
# v3.7 o perímetro do imóvel respondia por 0,17% da tinta e as camadas de
# contexto por 4%.
# None = sem recorte (padrão): APP e vegetação aparecem em todo o quadro.
# Um valor em metros volta a limitá-las ao imóvel mais essa faixa.
RECORTE_AMBIENTAL_M = None

# Faixa de APP padrão (m) — Lei 12.651/2012, Art. 4º, I, "a": 30 m para cursos
# d'água com menos de 10 m de largura, que é o caso da esmagadora maioria das
# drenagens em escala de propriedade rural no PR. Ajustável por --app-faixa.
APP_FAIXA_PADRAO_M = 30.0

CRS_MAPA = "EPSG:31982"       # SIRGAS 2000 / UTM 22S
CRS_GEOGRAFICO = "EPSG:4674"  # SIRGAS 2000 (lat/lon da anamnese)

# Busca ao vivo de pontos de referência no OpenStreetMap (API Overpass).
# Sem chave de API. Se o servidor oficial estiver lento, há espelhos:
#   https://overpass.kumi.systems/api/interpreter
#   https://overpass.private.coffee/api/interpreter
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
LIMITE_REFERENCIAS_OSM = 200

XYZ_SATELITE = (
    "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=15&zmin=0"
)


# ===========================================================================
# GEOMETRIA — espelho do modelo_mapa_situacaof.qpt, em milímetros.
# ===========================================================================

PAGE_W, PAGE_H = 297.0, 210.0

FONTE_BASE = "DejaVu Sans"
FONTE_UI = "MS Shell Dlg 2"

# --- Mapa principal --------------------------------------------------------
MAPA_X, MAPA_Y, MAPA_W, MAPA_H = 6.0, 6.0, 195.0, 195.0
MAPA_MOLDURA_MM = 0.4

# --- Grade de coordenadas --------------------------------------------------
GRADE_FRAME_WIDTH = 2.0
GRADE_FRAME_PEN = 0.3
GRADE_ANOT_DISTANCIA = 1.0
GRADE_ANOT_PRECISAO = 2
GRADE_ANOT_FONTE_PT = 8.25
GRADE_CROSS_LENGTH = 3.0

# --- Cabeçalho -------------------------------------------------------------
CAB_BOX = (205.0, 6.0, 86.0, 14.2)
CAB_LBL = (207.0, 6.0, 82.0, 14.2)
CAB_FONTE_PT = 16

# --- Caixa de identificação — v3.4: subiu para 21,590 (altura 44,00) -------
ID_BOX = (205.0, 21.590, 86.0, 44.0)
ID_LBL = (207.0, 22.690, 82.0, 41.8)
ID_FONTE_FALLBACKS = (7, 6.5, 6, 5.5)

# --- Localização — v3.5: mini-mapa reenquadrado (proporção casada) ---------
LOC_BOX = (205.0, 67.599, 86.0, 53.88)
LOC_LBL = (206.5, 68.599, 83.0, 4.0)
LOC_MAPA = (206.5, 71.263, 83.0, 49.763)

# Extensão FIXA do mini-mapa (xmin, ymin, xmax, ymax em EPSG:31982), validada
# no Compositor. Substitui o cálculo por folga relativa sobre o bbox da camada
# de municípios: aquele resultado não respeitava a proporção do quadro e o
# contorno do estado saía deformado/mal enquadrado.
#
# 783.355,789 x 469.667,279 m -> proporção 1,66790
#  83,000 x 49,763 mm         -> proporção 1,66791
# Com as duas proporções iguais, o QGIS não precisa esticar nada.
#
# O Paraná não muda de lugar, então fixar estes números é seguro e deixa o
# mini-mapa idêntico em todos os processos. Se a camada de municípios não
# carregar, o script cai no cálculo automático (LOC_FOLGA) como plano B.
LOC_EXTENT = (76241.733, 7042408.325, 859597.522, 7512075.604)
LOC_FOLGA = 0.18

# --- Rosa dos ventos -------------------------------------------------------
ROSA_FUNDO = (10.0, 10.0, 26.0, 26.0)
ROSA_IMG = (13.12, 13.12, 19.76, 19.76)

# --- Escala — v3.4: subiu 3,468. O rótulo ESCALA foi para 124,612, mantendo
#     a convenção de +1,0 mm do topo da caixa usada em LOCALIZAÇÃO e LEGENDA
#     (no .qpt ele tinha ficado para trás, em 128,080). -------------------
ESC_BOX = (205.0, 123.612, 86.0, 18.3074)
ESC_LBL = (206.5, 124.612, 10.5, 4.0)
ESC_NUM = (240.783, 125.369, 30.1294, 4.5)
ESC_NUM_FONTE_PT = 9
ESC_BAR = (237.544, 129.869, 39.7891, 12.2)
ESC_BAR_ALTURA = 3.5
ESC_BAR_SEGMENTOS = 4

# Unidades "de régua" para os segmentos da barra gráfica. Até a v3.9 o segmento
# saía de intervalo_da_grade/4, que a 1:10.000 dava 125 m -> barra de 50 mm num
# retângulo de 39,8 mm: ela vazava a caixa ESCALA e parecia deslocada.
# Agora o script escolhe o par (nº de segmentos, metros por segmento) que
# produz a MAIOR barra que ainda cabe em ESC_BAR_LARGURA_ALVO_MM, usando só
# valores redondos. A barra volta a ter o tamanho de antes e os rótulos ficam
# em números cheios (0 / 100 / 200 / 300 m).
UNIDADES_ESCALA_M = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500,
                     1000, 2000, 2500, 5000, 10000, 20000]
ESC_BAR_LARGURA_ALVO_MM = 37.0


def segmento_escala(escala, largura_alvo_mm=ESC_BAR_LARGURA_ALVO_MM):
    """
    Devolve (n_segmentos, metros_por_segmento, largura_mm) para a barra
    gráfica. Empate de largura resolve pelo maior número de segmentos, que dá
    uma régua mais fácil de ler.
    """
    melhor = None
    for n in (2, 3, 4, 5):
        for u in UNIDADES_ESCALA_M:
            largura = n * u / float(escala) * 1000.0
            if largura > largura_alvo_mm + 1e-9:
                continue
            if (melhor is None
                    or largura > melhor[2] + 1e-9
                    or (abs(largura - melhor[2]) < 1e-9 and n > melhor[0])):
                melhor = (n, u, largura)
    return melhor or (2, UNIDADES_ESCALA_M[0],
                      2 * UNIDADES_ESCALA_M[0] / float(escala) * 1000.0)
ESC_BAR_FONTE_PT = 6
ESC_BAR_LABEL_SPACE = 2.0
ESC_BAR_BOX_SPACE = 1.0
ESC_BAR_MIN_W, ESC_BAR_MAX_W = 50.0, 150.0
ESC_BAR_OUTLINE = 0.3

# --- Legenda — v3.4: subiu para 144,038 (2 colunas, até 8 entradas) --------
LEG_BOX = (205.0, 144.038, 86.0, 26.24)
LEG_LBL = (206.5, 145.028, 83.0, 4.0)
LEG_FONTE_PT = 6
LEG_LINHA_H = 5.08
LEG_Y0 = 149.528                      # topo do rótulo da 1ª linha
LEG_COLUNAS = (
    # (x_swatch, x_texto, largura_texto)
    (207.5, 217.0, 29.0),
    (248.5, 258.0, 31.0),
)
LEG_SWATCH_W = 8.0

# --- Rodapé (inalterado) ---------------------------------------------------
ROD_Y = 173.127
ROD_H = 31.1364
ROD_W = 41.5
ROD_BOX_ESQ_X = 205.0
ROD_BOX_DIR_X = 249.5
ROD_FONTE_PT = 6
ROD_COR = "#333333"
ROD_LOGO_GP = (215.758, 173.566, 18.0, 18.0)
ROD_ELABORADO = (207.0, 190.473, 37.5, 3.56228)
ROD_NOME_CARGO = (207.0, 194.035, 37.5, 5.6027)
ROD_FONTE_LBL = (251.448, 173.566, 8.8, 3.584)
ROD_LOGO_IAT = (260.418, 174.725, 27.7, 10.1157)
ROD_BASE_TXT = (251.856, 183.072, 36.2619, 20.0257)

RESPONSAVEL_NOME = "Guilherme Henrique Porfírio Santos"
RESPONSAVEL_CARGO = "Engenheiro Ambiental"

# --- Paleta ----------------------------------------------------------------
# O imóvel é o objeto do documento e tem o MAIOR contraste da prancha contra a
# imagem (2,25 contra a mata). Como esse é o teto, nenhuma camada de contexto
# pode passar dele — e o traço mais grosso é a alavanca mais barata para
# garantir a hierarquia sem saturar mais nenhuma cor.
COR_IMOVEL_FILL = "255,0,0,25"
COR_IMOVEL_LINHA = "255,0,0"
COR_IMOVEL_ESPESSURA = "0.7"
COR_VIAS = "255,170,0,255"
COR_HIDRO = "30,144,255,255"
# APP e vegetação são MANCHAS, não contornos: a leitura vem da área tingida e
# não da borda. Por isso o contorno é fino e da mesma família do preenchimento
# — borda grossa e saturada foi o que transformou cada córrego num verme ciano.
# Com o quadro fechado a ~1:6.000 a faixa de 30 m passa de 1,95 mm para ~4,9 mm
# e a mancha se lê sem precisar gritar.
COR_APP = "0,140,200,60"
COR_APP_LINHA = "0,120,175,150"

COR_VEG = "70,190,85,70"
# Contorno clareado a pedido (v3.11): de 45,130,55 para 90,170,100 — mesmo tom
# do preenchimento, só que opaco. Contraste contra a mata sobe de 1,87 para
# 3,15, então a borda aparece sem precisar engrossar.
#
# Isso passa do contorno do imóvel (2,25), mas não inverte a hierarquia: o que
# pesa no olho é contraste VEZES espessura. Imóvel: 2,25 x 0,7 mm = 1,58.
# Vegetação: 3,15 x 0,15 mm = 0,47. O imóvel continua três vezes mais forte.
COR_VEG_LINHA = "90,170,100,220"

COR_ESTRUTURA = "227,26,28,255"
COR_REFERENCIA = "255,255,0,255"
COR_COTA = "255,0,255,255"

_REFS_VIVAS = []


# ---------------------------------------------------------------------------
# Geometria do empreendimento
# ---------------------------------------------------------------------------

def _crs_mapa():
    return QgsCoordinateReferenceSystem(CRS_MAPA)


def _para_mapa(lat, lon):
    """Converte lat/lon (SIRGAS 2000 geográfico) para um QgsPointXY em CRS_MAPA."""
    p = QgsGeometry.fromPointXY(QgsPointXY(lon, lat))
    p.transform(QgsCoordinateTransform(
        QgsCoordinateReferenceSystem(CRS_GEOGRAFICO), _crs_mapa(), QgsProject.instance()
    ))
    return p.asPoint()


# ---------------------------------------------------------------------------
# ENTRADA DE COORDENADAS — GMS, GD ou UTM, detectado automaticamente
#
# O dado chega de fontes diferentes e em formatos diferentes:
#   * CAR / SIGEF  -> graus, minutos e segundos:  24°14'51.4"S 51°40'25.6"W
#   * Google Maps  -> graus decimais:             -24.247617, -51.673683
#   * QGIS         -> UTM 22S:                    571750, 7415750
#
# Em vez de obrigar a conversão manual antes de rodar (que é onde nascem os
# erros de dígito), o script aceita os três num campo só e descobre qual é.
# A separação é segura porque as faixas não se cruzam: longitude nunca passa
# de 180, e um easting UTM nunca é menor que 100.000.
# ---------------------------------------------------------------------------

PR_LAT = (-27.0, -22.0)
PR_LON = (-55.5, -47.5)
UTM_E = (100_000.0, 999_999.0)
UTM_N = (6_900_000.0, 7_700_000.0)


def _num_coord(txt):
    return float(txt.replace(".", "").replace(",", ".")) if re.match(r"^-?\d{1,3}(\.\d{3})+,\d+$", txt) \
        else float(txt.replace(",", "."))


def _sinal(valor, hemisferio):
    if hemisferio and hemisferio.upper() in ("S", "W", "O"):
        return -abs(valor)
    if hemisferio and hemisferio.upper() in ("N", "E", "L"):
        return abs(valor)
    return valor


def parse_coordenada(texto):
    """
    Aceita, numa string só:
      * GMS   24°14'51.4"S  51°40'25.6"W   (CAR, SIGEF)
      * GD    -24.247617, -51.673683       (Google Maps)
      * UTM   571750, 7415750              (QGIS, SIRGAS 2000 / UTM 22S)
      * URL do Google Maps
    Retorna (formato, lat, lon, utm_e, utm_n) — lat/lon ou utm vêm None
    conforme o formato; a conversão fica com quem chama.
    """
    if texto is None:
        return None
    t = str(texto).strip()
    if not t:
        return None

    # Link do Google Maps: .../@-24.2476,-51.6736,17z  ou  ?q=-24.24,-51.67
    m = re.search(r"[@=](-?\d+[.,]\d+),\s*(-?\d+[.,]\d+)", t)
    if "http" in t.lower() and m:
        t = f"{m.group(1)}, {m.group(2)}"

    t = (t.replace("º", "°").replace("′", "'").replace("´", "'")
          .replace("″", '"').replace("''", '"'))

    # --- GMS ---
    gms = re.findall(
        r"(-?\d{1,3})\s*°\s*(\d{1,2})\s*'\s*([\d.,]+)\s*\"?\s*([NSEWOLnsewol])?", t)
    if len(gms) < 2:
        gms = re.findall(
            r"(-?\d{1,3})\s+(\d{1,2})\s+([\d.,]+)\s*([NSEWOLnsewol])\b", t)
    if len(gms) >= 2:
        vals = []
        for g, mi, se, hemi in gms[:2]:
            dec = abs(float(g)) + float(mi) / 60.0 + _num_coord(se) / 3600.0
            if float(g) < 0:
                dec = -dec
            vals.append(_sinal(dec, hemi))
        lat, lon = _ordenar(vals[0], vals[1], gms[0][3], gms[1][3])
        return ("GMS", lat, lon, None, None)

    # Se o texto TEM marcas de GMS (° ' ") mas não formou dois pares completos,
    # é entrada truncada — recusa em vez de cair no parser decimal, que leria
    # 24°14'51.4"S como o par (24, 14) e devolveria um ponto no Saara.
    if re.search(r"[°'\"]", t):
        return None

    # --- números soltos: GD ou UTM ---
    nums = [_num_coord(x) for x in re.findall(r"-?\d+(?:[.,]\d+)?", t)]
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]

    e = next((v for v in (a, b) if UTM_E[0] <= abs(v) <= UTM_E[1]), None)
    n = next((v for v in (a, b) if UTM_N[0] <= abs(v) <= UTM_N[1]), None)
    if e is not None and n is not None:
        return ("UTM", None, None, abs(e), abs(n))

    if abs(a) <= 180 and abs(b) <= 180:
        lat, lon = _ordenar(a, b, None, None)
        return ("GD", lat, lon, None, None)

    return None


def _ordenar(v1, v2, h1, h2):
    """Decide qual é latitude e qual é longitude."""
    if h1 and h1.upper() in ("E", "W", "O", "L"):
        return v2, v1
    if h2 and h2.upper() in ("N", "S"):
        return v2, v1
    # Sem hemisfério: no Paraná a latitude é ~-24 e a longitude ~-51.
    if not (PR_LAT[0] <= v1 <= PR_LAT[1]) and (PR_LAT[0] <= v2 <= PR_LAT[1]):
        return v2, v1
    return v1, v2


def dentro_do_parana(lat, lon):
    return PR_LAT[0] <= lat <= PR_LAT[1] and PR_LON[0] <= lon <= PR_LON[1]


def coordenada_para_mapa(texto, rotulo="coordenada"):
    """
    Converte a entrada do usuário num QgsPointXY em CRS_MAPA.
    Retorna (ponto, formato_detectado). Levanta ValueError se não reconhecer.
    """
    r = parse_coordenada(texto)
    if r is None:
        raise ValueError(
            f"{rotulo}: não reconheci o formato de '{texto}'. "
            f"Aceito GMS (24°14'51.4\"S 51°40'25.6\"W), "
            f"graus decimais (-24.247617, -51.673683) ou UTM 22S (571750, 7415750)."
        )
    formato, lat, lon, e, n = r
    if formato == "UTM":
        return QgsPointXY(e, n), formato
    if not dentro_do_parana(lat, lon):
        print(f"[AVISO] {rotulo}: lat {lat:.6f} / lon {lon:.6f} cai FORA do Paraná. "
              f"Confira o sinal e a ordem dos valores.")
    return _para_mapa(lat, lon), formato


def _abrir_camada(spec, titulo, pista=None):
    """
    Abre uma camada vetorial a partir de um 'spec', que pode ser:
      * 'arquivo.gpkg|layername=camada'  -> usado como veio;
      * 'arquivo.gpkg' ou 'arquivo.shp'  -> abre direto; se o arquivo tiver
        várias camadas dentro, escolhe a que melhor casa com `pista` e avisa
        quais eram as opções.

    Retorna a QgsVectorLayer válida, ou None (com aviso no console).
    """
    if not spec:
        return None

    if "|layername=" in spec:
        lyr = QgsVectorLayer(spec, titulo, "ogr")
        if lyr.isValid():
            return lyr
        print(f"[aviso] '{titulo}': não abriu {spec}")
        return None

    caminho = Path(spec.split("|")[0])
    if not caminho.exists():
        print(f"[aviso] '{titulo}': arquivo não existe -> {spec}")
        return None

    lyr = QgsVectorLayer(spec, titulo, "ogr")
    sub = []
    try:
        sub = lyr.dataProvider().subLayers() if lyr.dataProvider() else []
    except Exception:
        sub = []

    # Um arquivo com uma camada só abre direto e válido.
    if lyr.isValid() and len(sub) <= 1:
        return lyr

    nomes = []
    for s in sub:
        # formato típico: "0!!::!!nome!!::!!contagem!!::!!geometria"
        partes = s.split("!!::!!") if "!!::!!" in s else s.split(":")
        if len(partes) >= 2:
            nomes.append(partes[1])
    nomes = [n for n in nomes if n]

    if not nomes:
        if lyr.isValid():
            return lyr
        print(f"[aviso] '{titulo}': não foi possível listar as camadas de {caminho.name}")
        return None

    escolhido = None
    if pista:
        alvo = pista.lower()
        escolhido = next((n for n in nomes if n.lower() == alvo), None)
        if escolhido is None:
            escolhido = next((n for n in nomes if alvo in n.lower() or n.lower() in alvo), None)
    if escolhido is None:
        escolhido = nomes[0]

    if len(nomes) > 1:
        print(f"[info] '{titulo}': {caminho.name} tem {len(nomes)} camadas "
              f"({', '.join(nomes[:8])}{'…' if len(nomes) > 8 else ''}); usando '{escolhido}'.")

    lyr = QgsVectorLayer(f"{caminho}|layername={escolhido}", titulo, "ogr")
    if lyr.isValid():
        return lyr
    print(f"[aviso] '{titulo}': camada '{escolhido}' não abriu em {caminho.name}")
    return None


def resolver_fontes(args):
    """
    Monta o dicionário {camada_lógica: spec} conforme a ordem de resolução
    descrita no bloco FONTES_CANDIDATAS, e imprime de onde cada camada veio —
    assim um caminho errado aparece no console antes de virar mapa vazio.
    """
    overrides = {
        "car": args.camada_car,
        "vias": args.camada_vias,
        "hidrografia": args.camada_hidrografia,
        "localidades": args.camada_localidades,
        "municipios": args.camada_municipios,
        "vegetacao": args.vegetacao,
    }

    base = None
    if args.dados_dir:
        base = Path(args.dados_dir)
    elif args.gpkg:
        base = Path(args.gpkg).parent

    fontes = {}
    for chave in FONTES_CANDIDATAS:
        spec, origem = None, None

        if overrides.get(chave):
            spec, origem = overrides[chave], "argumento"

        if spec is None and base is not None:
            for nome_arq in FONTES_CANDIDATAS[chave]:
                candidato = base / nome_arq
                if candidato.exists():
                    spec, origem = str(candidato), "pasta"
                    break

        if spec is None and args.gpkg and Path(args.gpkg).exists():
            spec, origem = f"{args.gpkg}|layername={NOMES_LOGICOS[chave]}", "gpkg combinado"

        fontes[chave] = spec
        if spec:
            print(f"[info] fonte '{chave}' ({origem}): {spec}")
        elif chave != "vegetacao":
            print(f"[aviso] fonte '{chave}': não encontrada. Procurei por "
                  f"{', '.join(FONTES_CANDIDATAS[chave])} em "
                  f"{base if base else '(nenhuma pasta informada)'}.")

    return fontes


def carregar_geometria_empreendimento(fontes, cod_imovel, lat, lon):
    """
    Retorna (geometria em CRS_MAPA, extensão, fonte:str, area_ha).

    Fonte é "car" ou "ponto_fallback". Conforme decisão fechada: o limite da
    propriedade vem EXCLUSIVAMENTE da base CAR — a matrícula foi descartada.
    """
    layer_car = _abrir_camada(fontes.get("car"), "Imóveis CAR", NOMES_LOGICOS["car"])
    if layer_car is not None and layer_car.isValid() and cod_imovel:
        layer_car.setSubsetString(f"\"{FIELD_COD_IMOVEL}\" = '{cod_imovel}'")
        feats = list(layer_car.getFeatures())
        if feats:
            feat = feats[0]
            geom = QgsGeometry(feat.geometry())
            geom.transform(QgsCoordinateTransform(layer_car.crs(), _crs_mapa(), QgsProject.instance()))
            area_ha = feat[FIELD_AREA_HA] if FIELD_AREA_HA in feat.fields().names() else None
            return geom, geom.boundingBox(), "car", area_ha
        print(f"[aviso] cod_imovel '{cod_imovel}' não encontrado na malha do CAR; usando fallback ponto+buffer.")
    elif layer_car is None or not layer_car.isValid():
        print("[aviso] malha do CAR não carregou; usando fallback ponto+buffer.")

    if lat is None or lon is None:
        raise ValueError(
            "Nenhuma feição do CAR encontrada e nenhuma coordenada (--lat/--lon) foi informada. "
            "Não é possível gerar o mapa sem geometria de referência."
        )
    ponto = QgsGeometry.fromPointXY(_para_mapa(lat, lon))
    buffer_geom = ponto.buffer(RAIO_CONTEXTO_M / 3, 20)
    return buffer_geom, buffer_geom.boundingBox(), "ponto_fallback", None


def extensao_com_margem(extent: QgsRectangle, margem_m=None) -> QgsRectangle:
    """
    Quadro do mapa principal: bounding box do IMÓVEL + margem fixa, forçado a
    quadrado porque o item de mapa é 195x195 mm.

    Antes isto multiplicava a extensão por 1,6 com piso de 3.000 m, o que
    enquadrava 3 km de território para uma propriedade de 900 m — o imóvel
    virava um detalhe no meio do mapa e tudo em volta competia com ele.
    """
    margem = MARGEM_ENQUADRAMENTO_M if margem_m is None else float(margem_m)
    cx, cy = extent.center().x(), extent.center().y()
    lado = max(max(extent.width(), extent.height()) + 2 * margem, LADO_MINIMO_M)
    _, lado = escala_redonda(lado)   # sobe para o próximo degrau redondo
    return QgsRectangle(cx - lado / 2, cy - lado / 2, cx + lado / 2, cy + lado / 2)


def extensao_com_folga_relativa(extent: QgsRectangle, fator=0.06) -> QgsRectangle:
    cx, cy = extent.center().x(), extent.center().y()
    largura = extent.width() * (1 + fator)
    altura = extent.height() * (1 + fator)
    return QgsRectangle(cx - largura / 2, cy - altura / 2, cx + largura / 2, cy + altura / 2)


# ---------------------------------------------------------------------------
# Rotulagem
# ---------------------------------------------------------------------------

def _aplicar_rotulo(layer, campo, tamanho_pt=6.0, cor="#ffffff", cor_buffer="#000000",
                    posicao="linha", deslocamento_y=0.0):
    """
    Liga rótulos na camada usando `campo`, com halo (buffer) para o texto
    sobreviver por cima da imagem de satélite.

    posicao: "linha"  -> texto acompanha a linha (vias, hidrografia)
             "acima"  -> texto acima do ponto (estrutura física, referências)
    """
    if layer is None or campo not in layer.fields().names():
        return

    fmt = QgsTextFormat()
    fonte = QFont(FONTE_BASE)
    fonte.setPointSizeF(tamanho_pt)
    fmt.setFont(fonte)
    fmt.setSize(tamanho_pt)
    fmt.setSizeUnit(_UN_RENDER_PT)
    fmt.setColor(QColor(cor))

    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setSizeUnit(_UN_RENDER_MM)
    buf.setColor(QColor(cor_buffer))
    fmt.setBuffer(buf)

    cfg = QgsPalLayerSettings()
    cfg.setFormat(fmt)
    cfg.fieldName = campo
    cfg.enabled = True

    if posicao == "linha":
        try:
            cfg.placement = _LBL_LINHA
        except AttributeError:
            pass
        # Texto acompanhando a linha, acima dela e sempre legível no sentido
        # do mapa. A API mudou de lugar entre versões do QGIS 3.x — por isso
        # as duas tentativas.
        flags = _LBL_ACIMA_DA_LINHA | _LBL_ORIENTACAO_MAPA
        try:
            cfg.lineSettings().setPlacementFlags(flags)
        except (AttributeError, TypeError):
            try:  # QGIS 3 antigo: flags direto no QgsPalLayerSettings
                cfg.placementFlags = flags
            except (AttributeError, TypeError):
                pass
    else:
        try:
            cfg.placement = _LBL_SOBRE_PONTO
        except AttributeError:
            pass
        # Quadrante "acima" do ponto
        try:
            cfg.quadOffset = _LBL_QUADRANTE_ACIMA
        except AttributeError:
            pass
        cfg.dist = 1.2

    if deslocamento_y:
        cfg.yOffset = deslocamento_y

    layer.setLabeling(QgsVectorLayerSimpleLabeling(cfg))
    layer.setLabelsEnabled(True)


# ---------------------------------------------------------------------------
# Camadas do mapa
# ---------------------------------------------------------------------------

def montar_camadas(fontes, geom_empreendimento, extent, fonte_geom,
                   ponto_estrutura=None, app_faixa=APP_FAIXA_PADRAO_M,
                   buscar_osm=False, osm_timeout=40, osm_url=OVERPASS_URL):
    """Cria e estiliza todas as camadas do mapa. Retorna (dict de layers, dict de métricas)."""
    layers = {}
    metricas = {}

    # --- Basemap de satélite ---
    basemap = QgsRasterLayer(XYZ_SATELITE, "Satélite", "wms")
    if basemap.isValid():
        basemap.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    layers["basemap"] = basemap

    # --- Polígono do imóvel (base CAR) ---
    nome_imovel = "Área do imóvel" if fonte_geom == "car" else "Ponto de referência"
    vl = QgsVectorLayer("Polygon?crs=" + CRS_MAPA, nome_imovel, "memory")
    vl.dataProvider().addFeature(_feature(geom_empreendimento))
    vl.updateExtents()
    # Contorno mais grosso: o imóvel é o objeto do documento e precisa dominar
    # a leitura. Preenchimento quase nulo para não competir com o satélite.
    vl.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
        "color": COR_IMOVEL_FILL, "outline_color": COR_IMOVEL_LINHA,
        "outline_width": COR_IMOVEL_ESPESSURA,
    })))
    layers["empreendimento"] = vl

    # --- Vias de acesso (com o nome real, atributo `name`) ---
    layers["vias"] = _recortar_camada_contexto(
        fontes.get("vias"), NOMES_LOGICOS["vias"], "Vias de acesso", extent,
        QgsLineSymbol.createSimple({"color": "255,170,0", "width": "0.6"}),
        campos_nome=FIELD_VIA_NOMES, rotulo_padrao=VIA_ROTULO_PADRAO,
    )
    _aplicar_rotulo(layers.get("vias"), "rotulo", 5.5, cor="#ffffff", posicao="linha")

    # --- Corpos hídricos (com o nome real, atributo `noriocomp`) ---
    layers["hidrografia"] = _recortar_camada_contexto(
        fontes.get("hidrografia"), NOMES_LOGICOS["hidrografia"], "Corpos hídricos", extent,
        QgsLineSymbol.createSimple({"color": "30,144,255", "width": "0.8"}),
        campos_nome=FIELD_HIDRO_NOMES, rotulo_padrao=HIDRO_ROTULO_PADRAO,
    )
    _aplicar_rotulo(layers.get("hidrografia"), "rotulo", 5.5, cor="#cdeaff", posicao="linha")

    # --- APP: buffer sobre a hidrografia (Lei 12.651/2012) ---
    if layers.get("hidrografia") is not None:
        layers["app"] = _buffer_app(layers["hidrografia"], app_faixa, geom_empreendimento)
        metricas["app_faixa"] = app_faixa

    # --- Remanescente vegetal (SICAR) ---
    if fontes.get("vegetacao"):
        veg_layer, veg_ha = _carregar_vegetacao(fontes["vegetacao"], geom_empreendimento, extent)
        if veg_layer is not None:
            layers["vegetacao"] = veg_layer
            metricas["vegetacao_ha"] = veg_ha

    # --- Pontos de referência (localidades OSM) ---
    layers["referencias"] = _recortar_camada_contexto(
        fontes.get("localidades"), NOMES_LOGICOS["localidades"], "Pontos de referência", extent,
        QgsMarkerSymbol.createSimple({
            "name": "square", "color": COR_REFERENCIA,
            "outline_color": "0,0,0,255", "outline_width": "0.2", "size": "1.8",
        }),
        campos_nome=FIELD_LOCALIDADE_NOMES, rotulo_padrao=LOCALIDADE_ROTULO_PADRAO,
        tipo_geom="Point", rotulo_unico=False,
    )
    if buscar_osm:
        achados = _buscar_referencias_osm(extensao_com_folga_relativa(extent, 0.2), osm_timeout, osm_url)
        layers["referencias"], _ = _mesclar_referencias(layers.get("referencias"), achados)
    _aplicar_rotulo(layers.get("referencias"), "rotulo", 5.5, cor="#ffffff", posicao="acima")

    # --- Estrutura física (ponto da anamnese) ---
    if ponto_estrutura is not None:
        est = QgsVectorLayer("Point?crs=" + CRS_MAPA, "Estrutura física", "memory")
        est.dataProvider().addAttributes([QgsField("rotulo", _TIPO_TEXTO)])
        est.updateFields()
        f = QgsFeature(est.fields())
        f.setGeometry(QgsGeometry.fromPointXY(ponto_estrutura))
        f["rotulo"] = "Estrutura física"
        est.dataProvider().addFeature(f)
        est.updateExtents()
        est.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
            "name": "circle", "color": COR_ESTRUTURA,
            "outline_color": "255,255,255,255", "outline_width": "0.4", "size": "3.0",
        })))
        _aplicar_rotulo(est, "rotulo", 7.0, cor="#ffffff", posicao="acima")
        layers["estrutura"] = est

        # --- Cota de distância até o corpo hídrico mais próximo ---
        cota_layer, dist_m, nome_rio = _cota_ate_hidrografia(ponto_estrutura, layers.get("hidrografia"))
        if cota_layer is not None:
            layers["cota_hidrica"] = cota_layer
            metricas["dist_hidrica_m"] = dist_m
            metricas["hidrica_nome"] = nome_rio

    return {k: v for k, v in layers.items() if v is not None}, metricas


def _buscar_referencias_osm(extent, timeout=40, url=OVERPASS_URL):
    """
    Busca pontos de referência ao vivo na API Overpass do OpenStreetMap,
    dentro da área de contexto do mapa.

    Complementa o `localidades_pontos.gpkg`, que só traz localidades (place=*)
    e é um instantâneo estático. A Overpass devolve também o que serve de
    amarração num croqui rural e costuma faltar no arquivo local: escolas,
    igrejas, capelas, cemitérios, pontes, torres, silos e atrativos.

    Retorna lista de (QgsPointXY em CRS_MAPA, nome). Devolve [] em qualquer
    falha — rede fora, servidor ocupado, resposta inesperada. O mapa nunca
    deixa de ser gerado por causa desta consulta.
    """
    import json
    import urllib.parse
    import urllib.request
    import urllib.error

    # A Overpass trabalha em WGS84 e espera o bbox como sul,oeste,norte,leste.
    para_wgs = QgsCoordinateTransform(
        _crs_mapa(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
    )
    de_wgs = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), _crs_mapa(), QgsProject.instance()
    )
    try:
        bb = para_wgs.transformBoundingBox(extent)
    except Exception as e:
        print(f"[aviso] Overpass: não foi possível converter a área para WGS84 ({e}).")
        return []
    bbox = f"{bb.yMinimum():.6f},{bb.xMinimum():.6f},{bb.yMaximum():.6f},{bb.xMaximum():.6f}"

    consulta = f"""[out:json][timeout:{int(timeout)}];
(
  node["place"]["name"]({bbox});
  node["amenity"~"^(school|kindergarten|place_of_worship|hospital|clinic|police|fire_station|townhall|community_centre|cemetery)$"]["name"]({bbox});
  way["amenity"~"^(school|place_of_worship|cemetery)$"]["name"]({bbox});
  node["man_made"~"^(water_tower|silo|storage_tank|mast|tower|windmill)$"]["name"]({bbox});
  node["tourism"]["name"]({bbox});
  node["historic"]["name"]({bbox});
  way["landuse"="cemetery"]["name"]({bbox});
  way["man_made"="bridge"]["name"]({bbox});
);
out center {LIMITE_REFERENCIAS_OSM};"""

    print(f"[info] Overpass: consultando pontos de referência em {bbox} …")
    try:
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"data": consulta}).encode("utf-8"),
            headers={"User-Agent": "GP-Consultoria-Ambiental/mapa-situacao (QGIS)"},
        )
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[aviso] Overpass respondeu HTTP {e.code} — seguindo só com a base local. "
              f"(429 ou 504 = servidor ocupado; tente de novo em alguns minutos.)")
        return []
    except Exception as e:
        print(f"[aviso] Overpass indisponível ({e}) — seguindo só com a base local.")
        return []

    achados = []
    for el in dados.get("elements", []):
        nome = (el.get("tags") or {}).get("name")
        if not nome:
            continue
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"].get("lat"), el["center"].get("lon")
        else:
            continue
        if lat is None or lon is None:
            continue
        try:
            p = QgsGeometry.fromPointXY(QgsPointXY(float(lon), float(lat)))
            p.transform(de_wgs)
            achados.append((p.asPoint(), str(nome).strip()))
        except Exception:
            continue

    print(f"[info] Overpass: {len(achados)} pontos de referência retornados.")
    return achados


def _mesclar_referencias(layer, achados, dist_min=60.0):
    """
    Junta os pontos da Overpass à camada de referências já montada do arquivo
    local, descartando duplicatas — o mesmo lugarejo costuma existir nas duas
    bases, e dois rótulos idênticos sobrepostos ficam ilegíveis.

    Duplicata = mesmo nome (ignorando caixa) a menos de `dist_min` metros.
    """
    if not achados:
        return layer, 0

    existentes = []
    if layer is not None:
        for f in layer.getFeatures():
            g = f.geometry()
            if g and not g.isEmpty():
                try:
                    existentes.append((g.asPoint(), (f["rotulo"] or "").strip().lower()))
                except (KeyError, IndexError):
                    existentes.append((g.asPoint(), ""))

    if layer is None:
        layer = QgsVectorLayer("Point?crs=" + CRS_MAPA, "Pontos de referência", "memory")
        layer.dataProvider().addAttributes([QgsField("rotulo", _TIPO_TEXTO)])
        layer.updateFields()
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
            "name": "square", "color": COR_REFERENCIA,
            "outline_color": "0,0,0,255", "outline_width": "0.2", "size": "1.8",
        })))

    novos = []
    for ponto, nome in achados:
        chave = nome.strip().lower()
        duplicado = any(
            chave == nome_ex and ponto.distance(p_ex) < dist_min
            for p_ex, nome_ex in existentes
        )
        if duplicado:
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(ponto))
        f["rotulo"] = nome
        novos.append(f)
        existentes.append((ponto, chave))

    if novos:
        layer.dataProvider().addFeatures(novos)
        layer.updateExtents()
    print(f"[info] Pontos de referência: +{len(novos)} da Overpass "
          f"({len(achados) - len(novos)} descartados por duplicidade).")
    return layer, len(novos)


def _props_app():
    """Símbolo da APP: mancha azul translúcida, borda discreta."""
    return {"color": COR_APP, "outline_color": COR_APP_LINHA, "outline_width": "0.15"}


def _props_veg():
    """Símbolo do remanescente vegetal: mancha verde translúcida, borda discreta."""
    return {"color": COR_VEG, "outline_color": COR_VEG_LINHA, "outline_width": "0.15"}


def _primeiro_preenchido(feicao, campos):
    """
    Devolve o primeiro campo com conteúdo real, na ordem pedida.

    O OGR devolve vazio de várias formas conforme o driver e a origem do dado:
    None, a string 'NULL', 'None', ou só espaços. Tratar todas como vazio evita
    o mapa sair com um rótulo escrito literalmente "NULL" em cima do rio.
    """
    for campo in campos or []:
        try:
            valor = feicao[campo]
        except (KeyError, IndexError):
            continue
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto and texto.upper() not in ("NULL", "NONE", "NAN", "<NULL>"):
            return texto
    return ""


def _feature(geom, campos=None, valores=None):
    f = QgsFeature(campos) if campos is not None else QgsFeature()
    f.setGeometry(geom)
    if valores:
        for k, v in valores.items():
            f[k] = v
    return f


def _recortar_camada_contexto(spec, pista, titulo, extent, simbolo,
                              campos_nome=None, rotulo_padrao="", tipo_geom="LineString",
                              rotulo_unico=True):
    """
    Copia só as feições dentro da extensão de contexto, com filtro espacial
    direto (QgsFeatureRequest + setFilterRect).

    Diferença da v2: em vez de descartar TODOS os atributos
    (`setSubsetOfAttributes([])`), pede apenas o campo de nome e o copia para
    um campo `rotulo` na camada de destino. É o que destrava a rotulagem
    exigida pela norma ("vias de acesso PRINCIPAIS", "pontos de referência").
    Um único atributo por feição não recria o problema de memória que o
    descarte total resolvia.
    """
    origem = _abrir_camada(spec, titulo, pista)
    if origem is None or not origem.isValid():
        print(f"[aviso] '{titulo}' ficará fora do mapa (fonte não carregou).")
        return None

    destino = QgsVectorLayer(f"{tipo_geom}?crs=" + CRS_MAPA, titulo, "memory")
    prov = destino.dataProvider()
    prov.addAttributes([QgsField("rotulo", _TIPO_TEXTO)])
    destino.updateFields()

    transform_to_map = QgsCoordinateTransform(origem.crs(), _crs_mapa(), QgsProject.instance())
    transform_back = QgsCoordinateTransform(_crs_mapa(), origem.crs(), QgsProject.instance())
    # Busca 20% além do quadro para que linhas que entram pela borda sejam
    # desenhadas inteiras; o QGIS recorta o excedente na renderização.
    extent_busca_origem = transform_back.transformBoundingBox(
        extensao_com_folga_relativa(extent, fator=0.2))

    req = QgsFeatureRequest()
    req.setFilterRect(extent_busca_origem)
    req.setLimit(LIMITE_FEICOES_CONTEXTO + 1)
    nomes_disponiveis = origem.fields().names()
    campos_uso = [c for c in (campos_nome or []) if c in nomes_disponiveis]
    faltando = [c for c in (campos_nome or []) if c not in nomes_disponiveis]
    if faltando:
        print(f"[aviso] campo(s) {faltando} não existe(m) na fonte de '{titulo}' "
              f"(campos: {', '.join(nomes_disponiveis[:12])}…).")
    if campos_uso:
        req.setSubsetOfAttributes(campos_uso, origem.fields())
    elif rotulo_padrao:
        # Nenhum campo de nome, mas há texto padrão: continua rotulando.
        req.setSubsetOfAttributes([])
    else:
        req.setSubsetOfAttributes([])

    novas, n, rotulados, com_padrao = [], 0, 0, 0
    for f in origem.getFeatures(req):
        n += 1
        if n > LIMITE_FEICOES_CONTEXTO:
            print(
                f"[aviso] '{titulo}' tem mais de {LIMITE_FEICOES_CONTEXTO} feições na área de contexto "
                f"— cortado no limite de segurança. Considere reduzir RAIO_CONTEXTO_M ou confirmar que "
                f"a fonte de '{titulo}' tem índice espacial."
            )
            break
        g = QgsGeometry(f.geometry())
        g.transform(transform_to_map)
        nf = QgsFeature(destino.fields())
        nf.setGeometry(g)
        texto = _primeiro_preenchido(f, campos_uso) or rotulo_padrao
        nf["rotulo"] = texto
        if texto:
            rotulados += 1
            if not _primeiro_preenchido(f, campos_uso):
                com_padrao += 1
        novas.append(nf)

    # Um rótulo por nome: o texto fica na feição mais longa de cada nome e as
    # demais ficam mudas. Sem isto "Córrego sem denominação" aparecia sete
    # vezes no mesmo quadro, e cada trecho de uma mesma estrada repetia o nome.
    if rotulo_unico:
        melhor = {}
        for idx, nf in enumerate(novas):
            txt = nf["rotulo"]
            if not txt:
                continue
            comp = nf.geometry().length() or 0.0
            if txt not in melhor or comp > melhor[txt][1]:
                melhor[txt] = (idx, comp)
        manter = {v[0] for v in melhor.values()}
        silenciados = 0
        for idx, nf in enumerate(novas):
            if nf["rotulo"] and idx not in manter:
                nf["rotulo"] = ""
                silenciados += 1
        if silenciados:
            print(f"[info] '{titulo}': {silenciados} rótulos repetidos silenciados "
                  f"({len(melhor)} nomes distintos).")

    prov.addFeatures(novas)
    destino.updateExtents()
    destino.setRenderer(QgsSingleSymbolRenderer(simbolo))
    if campos_uso or rotulo_padrao:
        extra = f", {com_padrao} com o texto padrão" if com_padrao else ""
        print(f"[info] '{titulo}': {len(novas)} feições, {rotulados} rotuladas{extra}.")
    return destino if novas else None


def _buffer_app(hidro_layer, faixa_m, geom_imovel=None):
    """
    Faixa de APP: buffer de `faixa_m` sobre cada feição de hidrografia,
    dissolvido num polígono só para não empilhar bordas sobrepostas.
    """
    geoms = [QgsGeometry(f.geometry()).buffer(faixa_m, 12) for f in hidro_layer.getFeatures()]
    if not geoms:
        return None
    uniao = QgsGeometry.unaryUnion(geoms)
    if uniao is None or uniao.isEmpty():
        return None

    # O recorte ao imóvel + 100 m foi REMOVIDO. Ele picotava a faixa contínua
    # em fragmentos soltos, que era o efeito de "polígonos sem nexo": o buffer
    # continuava correto, mas só sobrevivia onde encostava no imóvel, e o
    # resultado não se lia como faixa de APP nenhuma. A APP volta a acompanhar
    # toda a drenagem do quadro — que é como ela existe no terreno. Quem
    # delimita o que interessa é o enquadramento do mapa, não um recorte extra.
    if RECORTE_AMBIENTAL_M and geom_imovel is not None:
        zona = geom_imovel.buffer(RECORTE_AMBIENTAL_M, 12)
        uniao = uniao.intersection(zona)
        if uniao is None or uniao.isEmpty():
            print("[aviso] Nenhuma APP dentro do recorte ambiental. Camada fora do mapa.")
            return None

    app = QgsVectorLayer("Polygon?crs=" + CRS_MAPA, f"APP ({faixa_m:.0f} m)", "memory")
    app.dataProvider().addFeature(_feature(uniao))
    app.updateExtents()
    app.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(_props_app())))
    return app


def _carregar_vegetacao(caminho, geom_imovel, extent):
    """
    Carrega o arquivo de vegetação do SICAR, desenha o que cai na área de
    contexto e calcula a área de remanescente DENTRO do imóvel (ha).

    `caminho` aceita .shp, .gpkg ou 'arquivo.gpkg|layername=camada'.
    """
    veg = QgsVectorLayer(caminho, "vegetacao_origem", "ogr")
    if not veg.isValid():
        print(f"[aviso] arquivo de vegetação não carregou: {caminho}")
        return None, None

    transform_to_map = QgsCoordinateTransform(veg.crs(), _crs_mapa(), QgsProject.instance())
    transform_back = QgsCoordinateTransform(_crs_mapa(), veg.crs(), QgsProject.instance())
    extent_busca = transform_back.transformBoundingBox(
        extensao_com_folga_relativa(extent, fator=0.2))

    req = QgsFeatureRequest()
    req.setFilterRect(extent_busca)
    req.setSubsetOfAttributes([])

    destino = QgsVectorLayer("Polygon?crs=" + CRS_MAPA, "Remanescente vegetal", "memory")
    prov = destino.dataProvider()
    area_m2 = 0.0
    novas = []
    # Mesma decisão da APP: sem recorte por padrão, a mancha de vegetação é
    # desenhada inteira dentro do quadro. A ÁREA, essa sim, continua medida só
    # dentro do imóvel — é o número que vai para o cartucho e para a legenda.
    zona = (geom_imovel.buffer(RECORTE_AMBIENTAL_M, 12)
            if (RECORTE_AMBIENTAL_M and geom_imovel is not None) else None)
    for f in veg.getFeatures(req):
        g = QgsGeometry(f.geometry())
        g.transform(transform_to_map)
        if g.isEmpty():
            continue
        # Exibição recortada ao imóvel + faixa; a ÁREA continua medida só
        # dentro do imóvel, que é o número que vai ao cartucho.
        visivel = g.intersection(zona) if zona is not None else g
        if visivel and not visivel.isEmpty():
            novas.append(_feature(visivel))
        dentro = g.intersection(geom_imovel)
        if dentro and not dentro.isEmpty():
            area_m2 += dentro.area()

    if not novas:
        print("[aviso] nenhuma feição de vegetação na área de contexto.")
        return None, None

    prov.addFeatures(novas)
    destino.updateExtents()
    destino.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(_props_veg())))
    area_ha = area_m2 / 10000.0
    print(f"[info] Remanescente vegetal dentro do imóvel: {area_ha:.4f} ha "
          f"({len(novas)} feições desenhadas na área de contexto).")
    return destino, area_ha


def _cota_ate_hidrografia(ponto, hidro_layer):
    """
    Linha de cota entre a estrutura física e o corpo hídrico mais próximo,
    rotulada com a distância em metros. Atende a alínea 'c' da lista do mapa
    de situação, que pede a DISTÂNCIA dos corpos hídricos.
    """
    if hidro_layer is None:
        print("[aviso] sem camada de hidrografia; a cota de distância não será desenhada.")
        return None, None, None

    g_ponto = QgsGeometry.fromPointXY(ponto)
    melhor, melhor_dist, melhor_nome = None, None, None
    for f in hidro_layer.getFeatures():
        g = f.geometry()
        d = g_ponto.distance(g)
        if melhor_dist is None or d < melhor_dist:
            melhor_dist = d
            melhor = g_ponto.shortestLine(g)
            try:
                melhor_nome = f["rotulo"] or None
            except KeyError:
                melhor_nome = None

    if melhor is None or melhor_dist is None:
        return None, None, None

    cota = QgsVectorLayer("LineString?crs=" + CRS_MAPA, "Cota até corpo hídrico", "memory")
    cota.dataProvider().addAttributes([QgsField("rotulo", _TIPO_TEXTO)])
    cota.updateFields()
    f = QgsFeature(cota.fields())
    f.setGeometry(melhor)
    f["rotulo"] = f"{melhor_dist:,.1f} m".replace(",", "X").replace(".", ",").replace("X", ".")
    cota.dataProvider().addFeature(f)
    cota.updateExtents()
    cota.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
        "color": COR_COTA, "width": "0.5", "line_style": "dash",
    })))
    _aplicar_rotulo(cota, "rotulo", 6.5, cor="#ffffff", posicao="linha")
    print(f"[info] Distância da estrutura física ao corpo hídrico mais próximo: {melhor_dist:.1f} m"
          + (f" ({melhor_nome})" if melhor_nome else ""))
    return cota, melhor_dist, melhor_nome


def identificar_municipio(fontes, geom_empreendimento):
    """
    Retorna (nome_municipio, layer_estado, layer_destaque, extent_estado_utm).
    O extent do estado já vem reprojetado para CRS_MAPA.
    """
    mun = _abrir_camada(fontes.get("municipios"), "Municípios", NOMES_LOGICOS["municipios"])
    if mun is None or not mun.isValid():
        print("[aviso] camada de municípios não carregou; o mini-mapa ficará vazio.")
        return None, None, None, None

    mun.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
        "color": "225,225,225,255", "outline_color": "60,60,60", "outline_width": "0.12",
    })))

    transform_back = QgsCoordinateTransform(_crs_mapa(), mun.crs(), QgsProject.instance())
    transform_to_map = QgsCoordinateTransform(mun.crs(), _crs_mapa(), QgsProject.instance())

    geom_origem = QgsGeometry(geom_empreendimento)
    geom_origem.transform(transform_back)

    nome, destaque = None, None
    for f in mun.getFeatures():
        if f.geometry().intersects(geom_origem):
            nome = f[FIELD_MUNICIPIO_NOME] if FIELD_MUNICIPIO_NOME in f.fields().names() else None
            destaque = QgsVectorLayer("Polygon?crs=" + CRS_MAPA, "Município do empreendimento", "memory")
            g = QgsGeometry(f.geometry())
            g.transform(transform_to_map)
            destaque.dataProvider().addFeature(_feature(g))
            destaque.updateExtents()
            destaque.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
                "color": "230,30,30,220", "outline_color": "230,30,30", "outline_width": "0.3",
            })))
            break

    return nome, mun, destaque, transform_to_map.transformBoundingBox(mun.extent())


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def montar_layout(project, layers, extent, dados_cliente, logo_path, fonte_geom,
                  rosa_ventos_path=None, logo_fonte_path=None, extent_estado=None,
                  metricas=None):
    metricas = metricas or {}
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Mapa de Situação")

    page = layout.pageCollection().pages()[0]
    page.setPageSize(QgsLayoutSize(PAGE_W, PAGE_H, _UN_LAYOUT_MM))

    # ================== Mapa principal ==================
    mapa = QgsLayoutItemMap(layout)
    mapa.attemptSetSceneRect(QRectF(MAPA_X, MAPA_Y, MAPA_W, MAPA_H))
    mapa.setCrs(_crs_mapa())
    mapa.setExtent(extent)

    # ORDEM DE DESENHO — em setLayers(), o PRIMEIRO item fica POR CIMA.
    # Ordem definida pelo Guilherme, do topo para o fundo:
    #   estrutura física e cota  (pontos e medida, sempre visíveis)
    #   pontos de referência
    #   ÁREA DO IMÓVEL           (subiu: é o objeto do documento)
    #   vias de acesso
    #   corpos hídricos
    #   APP
    #   remanescente vegetal
    #   imagem de satélite
    # O contorno do imóvel agora cruza por cima das vias e da hidrografia,
    # deixando o perímetro legível de ponta a ponta — antes ele era cortado
    # nos pontos em que uma estrada ou um córrego atravessava a divisa.
    ordem = []
    for chave in ("estrutura", "cota_hidrica", "referencias", "empreendimento", "vias",
                  "hidrografia", "app", "vegetacao", "basemap"):
        if chave in layers and layers[chave] is not None:
            ordem.append(layers[chave])
    mapa.setLayers(ordem)
    print(f"[info] Ordem de desenho (topo -> fundo): {' > '.join(l.name() for l in ordem)}")

    mapa.setBackgroundEnabled(True)
    mapa.setFrameEnabled(True)
    mapa.setFrameStrokeColor(QColor(0, 0, 0))
    mapa.setFrameStrokeWidth(QgsLayoutMeasurement(MAPA_MOLDURA_MM, _UN_LAYOUT_MM))
    layout.addLayoutItem(mapa)
    _configurar_grade(mapa, extent)

    # ================== Cabeçalho ==================
    _add_box(layout, *CAB_BOX)
    _add_label(layout, "MAPA DE SITUAÇÃO", *CAB_LBL, tamanho=CAB_FONTE_PT,
               familia=FONTE_BASE, negrito=True, cor="#000000",
               alinhamento="centro", valinhamento="centro")

    # ================== Identificação ==================
    _add_box(layout, *ID_BOX)
    linhas_id = _linhas_identificacao(dados_cliente, metricas)
    texto_id, fonte_id = _ajustar_texto_na_caixa(linhas_id, ID_LBL[2], ID_LBL[3], ID_FONTE_FALLBACKS)
    _add_label(layout, texto_id, *ID_LBL, tamanho=fonte_id, familia=FONTE_BASE,
               cor="#000000", alinhamento="esquerda", valinhamento="topo")

    # ================== Localização ==================
    _add_box(layout, *LOC_BOX)
    _add_label(layout, "LOCALIZAÇÃO", *LOC_LBL, tamanho=LEG_FONTE_PT,
               familia=FONTE_BASE, negrito=True, alinhamento="esquerda", valinhamento="topo")

    locator = QgsLayoutItemMap(layout)
    locator.attemptSetSceneRect(QRectF(*LOC_MAPA))
    locator.setCrs(_crs_mapa())
    locator.setBackgroundEnabled(True)
    locator.setFrameEnabled(False)
    if layers.get("municipios_layer") is not None:
        loc_layers = []
        if layers.get("municipio_destaque") is not None:
            loc_layers.append(layers["municipio_destaque"])
        loc_layers.append(layers["municipios_layer"])
        locator.setLayers(loc_layers)
    if LOC_EXTENT:
        locator.setExtent(QgsRectangle(*LOC_EXTENT))
    elif extent_estado is not None and not extent_estado.isEmpty():
        locator.setExtent(extensao_com_folga_relativa(extent_estado, fator=LOC_FOLGA))
    layout.addLayoutItem(locator)

    # ================== Rosa dos ventos ==================
    fundo = QgsLayoutItemShape(layout)
    fundo.setShapeType(_FORMA_ELIPSE)
    fundo.attemptSetSceneRect(QRectF(*ROSA_FUNDO))
    fundo.setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,190", "outline_color": "0,0,0,120", "outline_width": "0.2",
    }))
    layout.addLayoutItem(fundo)

    norte = QgsLayoutItemPicture(layout)
    if rosa_ventos_path and Path(rosa_ventos_path).exists():
        norte.setPicturePath(str(rosa_ventos_path))
    else:
        if rosa_ventos_path:
            print(f"[aviso] --rosa-ventos aponta para arquivo inexistente: {rosa_ventos_path}")
        norte.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    norte.attemptSetSceneRect(QRectF(*ROSA_IMG))
    if hasattr(norte, "setResizeMode"):
        norte.setResizeMode(_FIGURA_ZOOM)
    layout.addLayoutItem(norte)

    # ================== Escala ==================
    _add_box(layout, *ESC_BOX)
    _add_label(layout, "ESCALA", *ESC_LBL, tamanho=LEG_FONTE_PT,
               familia=FONTE_BASE, negrito=True, alinhamento="esquerda", valinhamento="topo")

    # ================== Legenda (2 colunas) ==================
    _montar_legenda(layout, layers, metricas)

    # ================== Rodapé ==================
    _montar_rodape(layout, logo_path, logo_fonte_path, fonte_geom)

    # ================== Escala gráfica e numérica ==================
    extent_real = mapa.extent()
    intervalo = _intervalo_grade(extent_real.width())
    _add_barra_escala(layout, mapa)
    try:
        escala_val = mapa.scale()
        # Diagnóstico de enquadramento: quanto mede no papel o anel de 50 m do
        # Art. 68 (distância a divisas e a residências). Abaixo de ~5 mm o
        # analista não consegue medir, e a peça deixa de provar o que deveria.
        mm_50m = 50.0 / extent_real.width() * MAPA_W
        print(f"[info] Enquadramento: {extent_real.width():,.0f} m de largura | "
              f"escala 1:{escala_val:,.0f} | grade a cada {intervalo} m | "
              f"anel de 50 m = {mm_50m:.1f} mm no papel")
        escala_txt = f"1: {int(round(escala_val)):,}".replace(",", ".")
    except Exception as e:
        print(f"[aviso] Não foi possível calcular a escala numérica ({e}).")
        escala_txt = ""
    _add_label(layout, escala_txt, *ESC_NUM, tamanho=ESC_NUM_FONTE_PT,
               familia=FONTE_BASE, negrito=True, cor="#000000",
               alinhamento="centro", valinhamento="topo")

    return layout


def _montar_legenda(layout, layers, metricas):
    """
    Legenda desenhada à mão, agora em DUAS COLUNAS — a de uma coluna só
    comportava 3 entradas e o conjunto exigido pela IN 23/2026 chega a 7.
    (QgsLayoutItemLegend continua fora: setStyleFont já travou o QGIS sem
    traceback em testes anteriores.)
    """
    _add_box(layout, *LEG_BOX)
    _add_label(layout, "LEGENDA", *LEG_LBL, tamanho=LEG_FONTE_PT,
               familia=FONTE_BASE, negrito=True, alinhamento="esquerda", valinhamento="topo")

    faixa = metricas.get("app_faixa", APP_FAIXA_PADRAO_M)
    veg_ha = metricas.get("vegetacao_ha")
    rotulo_veg = "Remanescente vegetal"
    if veg_ha is not None:
        rotulo_veg = f"Remanesc. vegetal ({_num(veg_ha, 2)} ha)"

    entradas = []
    entradas.append(("Área do imóvel", "area",
                     {"color": COR_IMOVEL_FILL, "outline_color": COR_IMOVEL_LINHA,
                      "outline_width": COR_IMOVEL_ESPESSURA}))
    if "estrutura" in layers:
        entradas.append(("Estrutura física", "ponto",
                         {"color": COR_ESTRUTURA, "outline_color": "255,255,255,255", "outline_width": "0.3"}))
    if "vegetacao" in layers:
        entradas.append((rotulo_veg, "area", _props_veg()))
    if "app" in layers:
        entradas.append((f"APP ({faixa:.0f} m)", "area", _props_app()))
    if "hidrografia" in layers:
        entradas.append(("Corpos hídricos", "linha",
                         {"color": COR_HIDRO, "outline_color": COR_HIDRO, "outline_width": "0.1"}))
    if "vias" in layers:
        entradas.append(("Vias de acesso", "linha",
                         {"color": COR_VIAS, "outline_color": COR_VIAS, "outline_width": "0.1"}))
    if "referencias" in layers:
        entradas.append(("Pontos de referência", "ponto",
                         {"color": COR_REFERENCIA, "outline_color": "0,0,0,255", "outline_width": "0.2"}))
    if "cota_hidrica" in layers:
        entradas.append(("Cota até corpo hídrico", "linha",
                         {"color": COR_COTA, "outline_color": COR_COTA, "outline_width": "0.1"}))

    # Distribui em 2 colunas, preenchendo a primeira antes da segunda.
    por_coluna = -(-len(entradas) // len(LEG_COLUNAS))  # teto da divisão
    max_linhas = int((LEG_BOX[3] - 5.9) // LEG_LINHA_H)
    if por_coluna > max_linhas:
        print(f"[aviso] A legenda tem {len(entradas)} entradas e só cabem "
              f"{max_linhas * len(LEG_COLUNAS)} na caixa atual. As excedentes não serão desenhadas.")
        por_coluna = max_linhas

    for i, (texto, tipo, props) in enumerate(entradas):
        col, linha = divmod(i, por_coluna)
        if col >= len(LEG_COLUNAS):
            break
        x_swatch, x_texto, w_texto = LEG_COLUNAS[col]
        y_rotulo = LEG_Y0 + linha * LEG_LINHA_H

        if tipo == "linha":
            h_sw = 2.0
        elif tipo == "ponto":
            h_sw = 2.6
        else:
            h_sw = 3.5
        y_sw = y_rotulo + (LEG_LINHA_H - h_sw) / 2
        w_sw = 2.6 if tipo == "ponto" else LEG_SWATCH_W
        x_sw = x_swatch + (LEG_SWATCH_W - w_sw) / 2 if tipo == "ponto" else x_swatch

        sw = QgsLayoutItemShape(layout)
        sw.setShapeType(_FORMA_ELIPSE if tipo == "ponto" else _FORMA_RETANGULO)
        sw.attemptSetSceneRect(QRectF(x_sw, y_sw, w_sw, h_sw))
        sw.setSymbol(QgsFillSymbol.createSimple(props))
        layout.addLayoutItem(sw)

        _add_label(layout, texto, x_texto, y_rotulo, w_texto, LEG_LINHA_H,
                   tamanho=LEG_FONTE_PT, familia=FONTE_BASE,
                   alinhamento="esquerda", valinhamento="centro")


def _montar_rodape(layout, logo_path, logo_fonte_path, fonte_geom):
    _add_box(layout, ROD_BOX_ESQ_X, ROD_Y, ROD_W, ROD_H)
    _add_box(layout, ROD_BOX_DIR_X, ROD_Y, ROD_W, ROD_H)

    if logo_path and Path(logo_path).exists():
        logo = QgsLayoutItemPicture(layout)
        logo.setPicturePath(str(logo_path))
        logo.attemptSetSceneRect(QRectF(*ROD_LOGO_GP))
        if hasattr(logo, "setResizeMode"):
            logo.setResizeMode(_FIGURA_ZOOM)
        layout.addLayoutItem(logo)
    elif logo_path:
        print(f"[aviso] --logo aponta para arquivo inexistente: {logo_path}")

    _add_label(layout, "Elaborado por:", *ROD_ELABORADO, tamanho=ROD_FONTE_PT,
               familia=FONTE_UI, negrito=True, cor=ROD_COR,
               alinhamento="esquerda", valinhamento="topo")
    _add_label(layout, f"{RESPONSAVEL_NOME}\n{RESPONSAVEL_CARGO}", *ROD_NOME_CARGO,
               tamanho=ROD_FONTE_PT, familia=FONTE_UI, cor=ROD_COR,
               alinhamento="centro", valinhamento="topo")
    _add_label(layout, "Fonte:", *ROD_FONTE_LBL, tamanho=ROD_FONTE_PT,
               familia=FONTE_UI, negrito=True, cor=ROD_COR,
               alinhamento="esquerda", valinhamento="topo")

    if logo_fonte_path and Path(logo_fonte_path).exists():
        lf = QgsLayoutItemPicture(layout)
        lf.setPicturePath(str(logo_fonte_path))
        lf.attemptSetSceneRect(QRectF(*ROD_LOGO_IAT))
        if hasattr(lf, "setResizeMode"):
            lf.setResizeMode(_FIGURA_ZOOM)
        layout.addLayoutItem(lf)
    elif logo_fonte_path:
        print(f"[aviso] --logo-fonte aponta para arquivo inexistente: {logo_fonte_path}")

    fonte_txt = "Malha Fundiária CAR/SICAR" if fonte_geom == "car" else "Coordenada informada na anamnese"
    texto_base = "\n".join([
        "Base cartográfica: ",
        "IAT / ANA / OSM / SICAR",
        "Geometria: ",
        fonte_txt,
        "Sistema de Coordenadas:",          # ALTERADO v3 (era "...Geográficas:")
        "DATUM UTM - UTM 22S ",
        f"SIRGAS 2000 / {CRS_MAPA}",
    ])
    _add_label(layout, texto_base, *ROD_BASE_TXT, tamanho=ROD_FONTE_PT,
               familia=FONTE_BASE, cor=ROD_COR, alinhamento="esquerda", valinhamento="centro")


def _configurar_grade(mapa, extent):
    grade = mapa.grid()
    # Usa a extensão que o ITEM realmente desenha (mapa.extent()), e não a que
    # foi pedida: ao aplicar setExtent, o QGIS ajusta o retângulo à proporção
    # do quadro. Com quadro e extensão quadrados os dois coincidem, mas amarrar
    # na fonte real mantém grade, escala numérica e barra gráfica coerentes
    # entre si mesmo se o quadro deixar de ser quadrado.
    intervalo = _intervalo_grade(mapa.extent().width())
    grade.setEnabled(True)
    grade.setIntervalX(intervalo)
    grade.setIntervalY(intervalo)
    grade.setOffsetX(0)
    grade.setOffsetY(0)
    grade.setStyle(_GRADE_SO_MOLDURA)
    grade.setFrameStyle(_GRADE_MOLDURA_TICKS)
    grade.setFrameWidth(GRADE_FRAME_WIDTH)
    grade.setFramePenSize(GRADE_FRAME_PEN)
    grade.setFramePenColor(QColor(0, 0, 0))
    grade.setFrameFillColor1(QColor(255, 255, 255))
    grade.setFrameFillColor2(QColor(0, 0, 0))
    if hasattr(grade, "setFrameSideFlags"):
        grade.setFrameSideFlags(_GRADE_LADO_FLAG_ESQ
                                | _GRADE_LADO_FLAG_DIR
                                | _GRADE_LADO_FLAG_TOPO
                                | _GRADE_LADO_FLAG_BAIXO)
    grade.setCrossLength(GRADE_CROSS_LENGTH)
    grade.setAnnotationEnabled(True)
    grade.setAnnotationFormat(_GRADE_FMT_DECIMAL_SUFIXO)
    grade.setAnnotationPrecision(GRADE_ANOT_PRECISAO)
    grade.setAnnotationFrameDistance(GRADE_ANOT_DISTANCIA)

    fonte_grade = QFont(FONTE_UI)
    fonte_grade.setPointSizeF(GRADE_ANOT_FONTE_PT)
    if hasattr(grade, "setAnnotationTextFormat"):
        fmt = QgsTextFormat()
        fmt.setFont(fonte_grade)
        fmt.setSize(GRADE_ANOT_FONTE_PT)
        fmt.setSizeUnit(_UN_RENDER_PT)
        fmt.setColor(QColor(0, 0, 0))
        grade.setAnnotationTextFormat(fmt)
    elif hasattr(grade, "setAnnotationFont"):
        grade.setAnnotationFont(fonte_grade)
        grade.setAnnotationFontColor(QColor(0, 0, 0))

    for lado in (_GRADE_BORDA_ESQ, _GRADE_BORDA_DIR,
                 _GRADE_BORDA_TOPO, _GRADE_BORDA_BAIXO):
        if hasattr(grade, "setAnnotationPosition"):
            grade.setAnnotationPosition(_GRADE_FORA_DO_MAPA, lado)
        if hasattr(grade, "setAnnotationDisplay"):
            grade.setAnnotationDisplay(_GRADE_MOSTRAR_TUDO, lado)

    if hasattr(grade, "setAnnotationDirection"):
        grade.setAnnotationDirection(_GRADE_DIR_VERTICAL, _GRADE_BORDA_ESQ)
        grade.setAnnotationDirection(_GRADE_DIR_VERTICAL, _GRADE_BORDA_DIR)
        grade.setAnnotationDirection(_GRADE_DIR_HORIZONTAL, _GRADE_BORDA_TOPO)
        grade.setAnnotationDirection(_GRADE_DIR_HORIZONTAL, _GRADE_BORDA_BAIXO)


def _add_barra_escala(layout, mapa):
    """Barra gráfica: segmentos redondos, dimensionada para caber no quadro."""
    escala = QgsLayoutItemScaleBar(layout)
    escala.setLinkedMap(mapa)
    try:
        escala.setStyle("Double Box")
        if hasattr(escala, "style") and escala.style() != "Double Box":
            raise ValueError("estilo 'Double Box' não foi aplicado")
    except Exception as e:
        print(f"[aviso] Estilo 'Double Box' indisponível ({e}); usando 'Single Box'.")
        escala.setStyle("Single Box")

    escala.setUnits(_UN_METROS)
    escala.setUnitLabel("m")
    n_seg, metros_seg, largura_mm = segmento_escala(mapa.scale())
    escala.setNumberOfSegments(n_seg)
    escala.setNumberOfSegmentsLeft(0)
    escala.setUnitsPerSegment(metros_seg)
    print(f"[info] Barra de escala: {n_seg} x {metros_seg:g} m = "
          f"{n_seg * metros_seg:g} m ({largura_mm:.1f} mm de {ESC_BAR[2]:.1f} mm disponíveis)")
    escala.setMapUnitsPerScaleBarUnit(1)

    for attr, valor in (("setHeight", ESC_BAR_ALTURA), ("setLabelBarSpace", ESC_BAR_LABEL_SPACE),
                        ("setBoxContentSpace", ESC_BAR_BOX_SPACE), ("setMinimumBarWidth", ESC_BAR_MIN_W),
                        ("setMaximumBarWidth", ESC_BAR_MAX_W), ("setLineWidth", ESC_BAR_OUTLINE)):
        if hasattr(escala, attr):
            getattr(escala, attr)(valor)
    if hasattr(escala, "setSegmentSizeMode"):
        escala.setSegmentSizeMode(_BARRA_SEGMENTO_FIXO)
    if hasattr(escala, "setAlignment"):
        escala.setAlignment(_BARRA_ALINHA_ESQ)

    fonte_barra = QFont(FONTE_UI)
    fonte_barra.setPointSizeF(ESC_BAR_FONTE_PT)
    if hasattr(escala, "setTextFormat"):
        fmt = QgsTextFormat()
        fmt.setFont(fonte_barra)
        fmt.setSize(ESC_BAR_FONTE_PT)
        fmt.setSizeUnit(_UN_RENDER_PT)
        fmt.setColor(QColor(0, 0, 0))
        escala.setTextFormat(fmt)
    elif hasattr(escala, "setFont"):
        escala.setFont(fonte_barra)

    for attr, cor in (("setFillColor", QColor(0, 0, 0)), ("setFillColor2", QColor(255, 255, 255)),
                      ("setLineColor", QColor(0, 0, 0))):
        if hasattr(escala, attr):
            getattr(escala, attr)(cor)

    escala.attemptSetSceneRect(QRectF(*ESC_BAR))
    layout.addLayoutItem(escala)
    return escala


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------

def _num(valor, casas=4):
    """Formata número no padrão brasileiro (1.234,5678)."""
    txt = f"{valor:,.{casas}f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def _linhas_identificacao(d, metricas):
    """Linhas lógicas do cartucho, na ordem da IN 23/2026, Anexo II, seção II."""
    linhas = [
        f"Razão Social/Nome: {d.get('cliente', '—')}",
        f"CPF/CNPJ: {d.get('doc_cliente', '—')}",
        f"Endereço: {d.get('endereco', '—')}",
        f"Município: {d.get('municipio', '—')}",
    ]
    if d.get("cod_imovel"):
        linhas.append("Cód. imóvel (CAR):")
        linhas.append(d["cod_imovel"])
    if d.get("area_ha") is not None:
        linhas.append(f"Área total: {_num(d['area_ha'])} ha")
    if d.get("area_construida_ha") is not None:
        linhas.append(f"Área construída: {_num(d['area_construida_ha'])} ha")
    if d.get("area_livre_ha") is not None:
        linhas.append(f"Área livre: {_num(d['area_livre_ha'])} ha")
    if metricas.get("vegetacao_ha") is not None:
        veg = metricas["vegetacao_ha"]
        pct = ""
        if d.get("area_ha"):
            try:
                pct = f" ({_num(100 * veg / d['area_ha'], 1)}%)"
            except ZeroDivisionError:
                pct = ""
        linhas.append(f"Remanescente vegetal: {_num(veg)} ha{pct}")
    if metricas.get("dist_hidrica_m") is not None:
        nome = metricas.get("hidrica_nome")
        sufixo = f" ({nome})" if nome else ""
        linhas.append(f"Dist. corpo hídrico: {_num(metricas['dist_hidrica_m'], 1)} m{sufixo}")
    if d.get("utm_e") is not None and d.get("utm_n") is not None:
        linhas.append(f"Coordenadas UTM (E, N): {_num(d['utm_e'], 2)} / {_num(d['utm_n'], 2)} — 22S")
    return linhas


def _ajustar_texto_na_caixa(linhas, largura_mm, altura_mm, tamanhos):
    """
    Encaixa o texto na caixa de altura FIXA. Reduz a fonte em vez de esticar a
    caixa — assim LOCALIZAÇÃO, ESCALA, LEGENDA e rodapé ficam nas posições
    validadas no Compositor.
    """
    for pt in tamanhos:
        quebradas = _quebrar_linhas(linhas, largura_mm, tamanho_pt=pt)
        if len(quebradas) * _altura_linha_mm(pt) <= altura_mm:
            return "\n".join(quebradas), pt
    pt = tamanhos[-1]
    quebradas = _quebrar_linhas(linhas, largura_mm, tamanho_pt=pt)
    print(f"[aviso] O texto de identificação não coube na caixa nem a {pt}pt "
          f"({len(quebradas)} linhas). Encurte o endereço ou a razão social.")
    return "\n".join(quebradas), pt


def _quebrar_linhas(linhas, largura_mm, tamanho_pt=8):
    largura_media_char_mm = tamanho_pt * 0.21
    chars_por_linha = max(int(largura_mm / largura_media_char_mm), 8)
    resultado = []
    for linha in linhas:
        resultado.extend(textwrap.wrap(linha, width=chars_por_linha, break_long_words=True) or [""])
    return resultado


def _altura_linha_mm(tamanho_pt=8):
    return tamanho_pt * 0.58


def _intervalo_grade(largura_m):
    passos = [50, 100, 250, 500, 1000, 2000, 5000, 10000]
    alvo = largura_m / 5
    return min(passos, key=lambda p: abs(p - alvo))


def _add_label(layout, texto, x, y, w, h, tamanho=9, familia=FONTE_BASE, negrito=False,
               cor="#000000", alinhamento="esquerda", valinhamento="topo"):
    lbl = QgsLayoutItemLabel(layout)
    lbl.setText(texto)
    fonte = QFont(familia)
    fonte.setPointSizeF(float(tamanho))
    fonte.setBold(negrito)
    lbl.setFont(fonte)
    lbl.setFontColor(QColor(cor))
    lbl.setMarginX(0)
    lbl.setMarginY(0)
    if hasattr(lbl, "setHAlign"):
        lbl.setHAlign({"esquerda": Qt.AlignmentFlag.AlignLeft, "direita": Qt.AlignmentFlag.AlignRight,
                       "centro": Qt.AlignmentFlag.AlignHCenter}.get(alinhamento, Qt.AlignmentFlag.AlignLeft))
    if hasattr(lbl, "setVAlign"):
        lbl.setVAlign({"topo": Qt.AlignmentFlag.AlignTop, "centro": Qt.AlignmentFlag.AlignVCenter,
                       "base": Qt.AlignmentFlag.AlignBottom}.get(valinhamento, Qt.AlignmentFlag.AlignTop))
    lbl.attemptSetSceneRect(QRectF(x, y, w, h))
    layout.addLayoutItem(lbl)
    return lbl


def _add_box(layout, x, y, w, h):
    caixa = QgsLayoutItemShape(layout)
    caixa.setShapeType(_FORMA_RETANGULO)
    caixa.attemptSetSceneRect(QRectF(x, y, w, h))
    caixa.setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,0", "outline_color": "0,0,0,255",
        "outline_width": "0.2", "joinstyle": "bevel",
    }))
    layout.addLayoutItem(caixa)
    return caixa


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    """Gera o mapa. Chamado pela linha de comando (argv=None lê sys.argv) ou
    pelo rodar_mapa.py, que passa a lista de argumentos e usa o dicionário
    devolvido (área total, área livre, município da malha, avisos) para
    gravar de volta no Odoo.

    v3.13:
      * --area-livre-ha deixou de ser dado da anamnese (decisão D2): se não
        for informado e houver área construída, é calculado como
        área total do polígono do CAR − área construída.
      * O endereço aceita o marcador {municipio}, trocado pelo município da
        malha do IAT (decisão D4: a malha é sempre a fonte do município).
    """
    ap = argparse.ArgumentParser(description="Gera o mapa de situação (croqui) de um empreendimento.")
    ap.add_argument("--dados-dir", default=None,
                    help="Pasta com os .gpkg das camadas (imoveis_pr_poligonos_car.gpkg, "
                         "hidrografia_linhas_ANA.gpkg, vias_linhas_OpenStreetMap.gpkg, "
                         "localidades_pontos.gpkg, municipios_poligonos_iat.gpkg, "
                         "sicar_vegetacao_nativa.gpkg). É o jeito mais simples de rodar.")
    ap.add_argument("--gpkg", default=None,
                    help="GPKG combinado, usado só para as camadas não encontradas em --dados-dir")
    ap.add_argument("--camada-car", default=None, help="Sobrescreve a fonte da malha do CAR")
    ap.add_argument("--camada-vias", default=None, help="Sobrescreve a fonte das vias")
    ap.add_argument("--camada-hidrografia", default=None, help="Sobrescreve a fonte da hidrografia")
    ap.add_argument("--camada-localidades", default=None, help="Sobrescreve a fonte das localidades")
    ap.add_argument("--camada-municipios", default=None, help="Sobrescreve a fonte dos municípios")
    ap.add_argument("--cod-imovel", default=None, help="Código do imóvel no CAR (cod_imovel)")
    ap.add_argument("--lat", type=float, default=None, help="Latitude do empreendimento")
    ap.add_argument("--lon", type=float, default=None, help="Longitude do empreendimento")
    ap.add_argument("--estrutura-lat", type=float, default=None,
                    help="Latitude do ponto da estrutura física (anamnese). Sem isso, usa --lat.")
    ap.add_argument("--estrutura-lon", type=float, default=None,
                    help="Longitude do ponto da estrutura física (anamnese). Sem isso, usa --lon.")
    ap.add_argument("--estrutura-coord", default=None,
                    help="Coordenada da estrutura física em QUALQUER formato: GMS "
                         "(\"24°14'51.4\\\"S 51°40'25.6\\\"W\"), graus decimais "
                         "(\"-24.247617, -51.673683\"), UTM 22S (\"571750, 7415750\") "
                         "ou um link do Google Maps. O formato é detectado sozinho.")
    ap.add_argument("--estrutura-e", type=float, default=None,
                    help="Coordenada UTM E da estrutura física (EPSG:31982). Tem precedência sobre lat/lon.")
    ap.add_argument("--estrutura-n", type=float, default=None,
                    help="Coordenada UTM N da estrutura física (EPSG:31982). Tem precedência sobre lat/lon.")
    ap.add_argument("--cliente", required=True, help="Nome/Razão social do cliente")
    ap.add_argument("--doc-cliente", required=True, help="CPF ou CNPJ do cliente")
    ap.add_argument("--endereco", required=True, help="Endereço do cliente/empreendimento")
    ap.add_argument("--area-ha", type=float, default=None,
                    help="Área total em hectares (se omitida, usa a do CAR)")
    ap.add_argument("--area-construida-ha", type=float, default=None,
                    help="Área construída em HECTARES (anamnese)")
    ap.add_argument("--area-livre-ha", type=float, default=None,
                    help="Área livre em HECTARES (anamnese)")
    ap.add_argument("--utm-e", type=float, default=None,
                    help="Coordenada UTM E. Se omitida, é calculada do ponto da estrutura física.")
    ap.add_argument("--utm-n", type=float, default=None,
                    help="Coordenada UTM N. Se omitida, é calculada do ponto da estrutura física.")
    ap.add_argument("--vegetacao", default=None,
                    help="Sobrescreve a fonte da vegetação do SICAR (.shp, .gpkg ou 'arq.gpkg|layername=camada')")
    ap.add_argument("--osm-referencias", action="store_true",
                    help="Busca pontos de referência ao vivo no OpenStreetMap (API Overpass) "
                         "e mescla com a base local. Exige internet.")
    ap.add_argument("--osm-timeout", type=int, default=40,
                    help="Timeout da consulta Overpass, em segundos (padrão 40)")
    ap.add_argument("--osm-url", default=OVERPASS_URL,
                    help="Endpoint Overpass alternativo (espelho), se o oficial estiver lento")
    ap.add_argument("--margem-m", type=float, default=MARGEM_ENQUADRAMENTO_M,
                    help=f"Margem em metros ao redor do imóvel no enquadramento "
                         f"(padrão {MARGEM_ENQUADRAMENTO_M:.0f}; maior = mais contexto, escala menor)")
    ap.add_argument("--app-faixa", type=float, default=APP_FAIXA_PADRAO_M,
                    help=f"Faixa de APP em metros (padrão {APP_FAIXA_PADRAO_M:.0f}, Lei 12.651/2012)")
    ap.add_argument("--logo", default=None, help="PNG do logo GP (fundo transparente)")
    ap.add_argument("--logo-fonte", default=None, help="PNG da logo do IAT, ao lado de 'Fonte:'")
    ap.add_argument("--rosa-ventos", default=None, help="SVG da rosa dos ventos")
    ap.add_argument("--saida", required=True, help="Caminho do PDF/PNG de saída")
    ap.add_argument("--salvar-qpt", default=None, help="Salva o layout montado como modelo .qpt")
    args = ap.parse_args(argv)

    print(f"[info] gerar_mapa_situacao.py — versão {VERSAO}")
    # Eco dos parâmetros que definem o conteúdo do mapa. No Console do QGIS o
    # sys.argv sobrevive entre execuções, então um argumento velho pode gerar
    # um PDF novo com conteúdo antigo — e isso só aparece olhando o cartucho.
    # Com o eco, dá para conferir na primeira linha do log.
    print(f"[info] recebido: cod_imovel={args.cod_imovel} | "
          f"estrutura E/N={args.estrutura_e}/{args.estrutura_n} | "
          f"lat/lon={args.lat}/{args.lon} | saida={args.saida}")

    if not args.dados_dir and not args.gpkg:
        ap.error("informe --dados-dir (pasta com os .gpkg) ou --gpkg (GPKG combinado).")

    # Falha cedo e com mensagem clara em vez de gerar um mapa vazio: é o que
    # acontecia quando um caminho de exemplo (C:\...\) era colado literalmente.
    for rotulo, caminho in (("--dados-dir", args.dados_dir), ("--gpkg", args.gpkg),
                            ("--logo", args.logo), ("--logo-fonte", args.logo_fonte),
                            ("--rosa-ventos", args.rosa_ventos), ("--vegetacao", args.vegetacao)):
        if caminho and "..." in str(caminho):
            ap.error(f"{rotulo} recebeu um caminho de exemplo ainda com '...': {caminho}. "
                     f"Troque pelo caminho real do arquivo.")

    instancia_propria = QgsApplication.instance() is None
    if instancia_propria:
        qgs = QgsApplication([], False)
        qgs.initQgis()
    try:
        try:
            from qgis.core import QgsSettings
            QgsSettings().setValue("/qgis/parallel_rendering", False)
        except Exception as e:
            print(f"[aviso] Não foi possível desativar a renderização paralela: {e}")

        project = QgsProject.instance()
        project.setCrs(_crs_mapa())

        fontes = resolver_fontes(args)

        geom, extent, fonte_geom, area_ha_car = carregar_geometria_empreendimento(
            fontes, args.cod_imovel, args.lat, args.lon
        )
        extent_mapa = extensao_com_margem(extent, args.margem_m)

        # Ponto da estrutura física: o da anamnese; na falta, o lat/lon geral.
        ponto_estrutura = None
        formato_coord = None
        if args.estrutura_coord:
            ponto_estrutura, formato_coord = coordenada_para_mapa(
                args.estrutura_coord, "--estrutura-coord")
            print(f"[info] Coordenada da estrutura física lida como {formato_coord}: "
                  f"E {ponto_estrutura.x():,.2f} / N {ponto_estrutura.y():,.2f} (UTM 22S)")

        if ponto_estrutura is None and args.estrutura_e is not None and args.estrutura_n is not None:
            # UTM direto, sem conversão. Checagem de sanidade: no Paraná, em
            # UTM 22S, E fica na casa das centenas de milhar e N tem 7 dígitos
            # (~7.0 a 7.6 milhões). Um dígito a mais ou a menos joga o ponto
            # para fora do estado e passa despercebido no mapa.
            if not (100_000 <= args.estrutura_e <= 900_000):
                ap.error(f"--estrutura-e fora da faixa esperada para UTM 22S: {args.estrutura_e:,.0f} "
                         f"(esperado entre 100.000 e 900.000).")
            if not (6_900_000 <= args.estrutura_n <= 7_700_000):
                ap.error(f"--estrutura-n fora da faixa esperada para o Paraná: {args.estrutura_n:,.0f} "
                         f"(esperado entre 6.900.000 e 7.700.000 — confira o número de dígitos).")
            ponto_estrutura = QgsPointXY(args.estrutura_e, args.estrutura_n)

        est_lat = args.estrutura_lat if args.estrutura_lat is not None else args.lat
        est_lon = args.estrutura_lon if args.estrutura_lon is not None else args.lon
        if ponto_estrutura is not None:
            pass
        elif est_lat is not None and est_lon is not None:
            ponto_estrutura = _para_mapa(est_lat, est_lon)
        else:
            print("[aviso] Sem coordenada para a estrutura física — o ponto, o rótulo "
                  "'Estrutura física' e a cota de distância ficarão fora do mapa.")

        # Coerência entre o imóvel (CAR) e o ponto da estrutura: se estiverem
        # a quilômetros de distância, é quase certo que vieram de processos
        # diferentes — foi o que aconteceu no teste com um CAR de Abatiá e uma
        # coordenada de Ivaiporã, 171 km adiante. O mapa saía "correto" e o
        # erro só aparecia na cota de distância absurda.
        if ponto_estrutura is not None:
            d_imovel = QgsGeometry.fromPointXY(ponto_estrutura).distance(geom)
            if d_imovel > 0:
                print(f"[info] A estrutura física está {d_imovel:,.0f} m FORA do polígono do imóvel.")
            if d_imovel > 5000:
                print(f"[AVISO] A estrutura física está a {d_imovel/1000:,.1f} km do imóvel. "
                      f"Quase certamente o código do CAR e a coordenada são de processos "
                      f"diferentes — confira antes de usar este mapa.")

        layers, metricas = montar_camadas(
            fontes, geom, extent_mapa, fonte_geom,
            ponto_estrutura=ponto_estrutura,
            app_faixa=args.app_faixa,
            buscar_osm=args.osm_referencias,
            osm_timeout=args.osm_timeout,
            osm_url=args.osm_url,
        )

        municipio_nome, mun_layer, destaque_layer, extent_estado = identificar_municipio(fontes, geom)
        if mun_layer is not None and mun_layer.isValid():
            layers["municipios_layer"] = mun_layer
        if destaque_layer is not None:
            layers["municipio_destaque"] = destaque_layer

        for lyr in layers.values():
            if lyr is not None:
                project.addMapLayer(lyr, False)

        # Coordenadas UTM do cartucho.
        utm_e, utm_n = args.utm_e, args.utm_n
        if (utm_e is None or utm_n is None) and ponto_estrutura is not None:
            utm_e, utm_n = ponto_estrutura.x(), ponto_estrutura.y()
        if (utm_e is None or utm_n is None):
            centro = geom.centroid().asPoint()
            utm_e, utm_n = centro.x(), centro.y()
            print("[info] Coordenadas UTM do cartucho calculadas do centroide do imóvel "
                  "(sem ponto de estrutura física informado).")

        area_total_ha = args.area_ha if args.area_ha is not None else area_ha_car

        # Área livre (D2): total do polígono do CAR − construída.
        area_livre_ha = args.area_livre_ha
        if area_livre_ha is None and args.area_construida_ha is not None:
            if area_total_ha is None:
                print("[aviso] Sem área total (imóvel fora da malha do CAR): "
                      "área livre não calculada.")
            elif args.area_construida_ha > area_total_ha:
                raise ValueError(
                    f"Área construída ({args.area_construida_ha:.4f} ha) maior que a "
                    f"área total do imóvel ({area_total_ha:.4f} ha). Confira a anamnese.")
            else:
                area_livre_ha = round(area_total_ha - args.area_construida_ha, 4)
                print(f"[info] Área livre calculada: {area_total_ha:.4f} − "
                      f"{args.area_construida_ha:.4f} = {area_livre_ha:.4f} ha")

        # Município (D4): sempre o da malha do IAT.
        endereco = args.endereco
        if "{municipio}" in endereco:
            if municipio_nome:
                endereco = endereco.replace("{municipio}", municipio_nome)
            else:
                endereco = endereco.replace(" - {municipio}/PR", "").replace("{municipio}", "")
                print("[aviso] Município não identificado na malha: endereço sem município.")

        dados_cliente = {
            "cliente": args.cliente,
            "doc_cliente": args.doc_cliente,
            "endereco": endereco,
            "municipio": municipio_nome or "—",
            "cod_imovel": args.cod_imovel if fonte_geom == "car" else None,
            "area_ha": area_total_ha,
            "area_construida_ha": args.area_construida_ha,
            "area_livre_ha": area_livre_ha,
            "utm_e": utm_e,
            "utm_n": utm_n,
        }

        layout = montar_layout(
            project, layers, extent_mapa, dados_cliente, args.logo, fonte_geom,
            args.rosa_ventos, args.logo_fonte, extent_estado, metricas,
        )

        if args.salvar_qpt:
            doc_qpt = QDomDocument("qgis_layout")
            doc_qpt.appendChild(layout.writeXml(doc_qpt, QgsReadWriteContext()))
            caminho_qpt = Path(args.salvar_qpt)
            caminho_qpt.parent.mkdir(parents=True, exist_ok=True)
            caminho_qpt.write_text(doc_qpt.toString(), encoding="utf-8")
            print(f"[ok] Modelo salvo em: {caminho_qpt}")

        # Dá tempo aos tiles XYZ chegarem antes de exportar.
        for _ in range(80):
            QCoreApplication.processEvents()
            time.sleep(0.15)

        exporter = QgsLayoutExporter(layout)
        saida = Path(args.saida)
        saida.parent.mkdir(parents=True, exist_ok=True)
        if saida.suffix.lower() == ".pdf":
            resultado = exporter.exportToPdf(str(saida), QgsLayoutExporter.PdfExportSettings())
        else:
            resultado = exporter.exportToImage(str(saida), QgsLayoutExporter.ImageExportSettings())

        if resultado != _EXPORT_OK:
            print(f"[erro] Falha ao exportar o mapa (código {resultado}).", file=sys.stderr)
            sys.exit(1)

        print(f"[ok] Mapa gerado: {saida}")
        print(f"[ok] Fonte da geometria: {fonte_geom}")
        print(f"[ok] Coordenadas UTM (E, N): {utm_e:.2f} / {utm_n:.2f} — 22S")
        if dados_cliente["area_ha"] is not None:
            print(f"[ok] Área total: {dados_cliente['area_ha']:.4f} ha")
        if metricas.get("vegetacao_ha") is not None:
            print(f"[ok] Remanescente vegetal no imóvel: {metricas['vegetacao_ha']:.4f} ha")
        if metricas.get("dist_hidrica_m") is not None:
            print(f"[ok] Distância ao corpo hídrico: {metricas['dist_hidrica_m']:.1f} m")

        return {
            "versao": VERSAO,
            "saida": str(saida),
            "fonte_geom": fonte_geom,
            "municipio": municipio_nome,
            "endereco": endereco,
            "area_total_ha": area_total_ha,
            "area_construida_ha": args.area_construida_ha,
            "area_livre_ha": area_livre_ha,
            "utm_e": utm_e,
            "utm_n": utm_n,
            "vegetacao_ha": metricas.get("vegetacao_ha"),
            "dist_hidrica_m": metricas.get("dist_hidrica_m"),
        }

    finally:
        if instancia_propria:
            qgs.exitQgis()


if __name__ == "__main__":
    main()
