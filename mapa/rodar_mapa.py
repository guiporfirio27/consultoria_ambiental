# -*- coding: utf-8 -*-
r"""
rodar_mapa.py — gera o Mapa de Situação de UM Processo, lendo tudo do Odoo.

COMO USAR no Console Python do QGIS (uma linha só):

    exec(open(r"D:\Guilherme\Guilherme\Documentos\Dados GIS\rodar_mapa.py", encoding="utf-8").read())

1. Troque PROCESSO_ID pelo número do Processo no Odoo (aparece na URL:
   .../odoo/project/66 → 66).
2. Salve e rode a linha acima. Não é preciso reiniciar o QGIS.

O que acontece:
  * lê Processo → Empreendimento → Imóvel → Empreendedor no Odoo;
  * confere se os dados estão completos e válidos (CAR, CPF/CNPJ,
    coordenada, vínculos). Faltando algo, NÃO gera e lista o que falta;
  * gera o PDF em  Dados GIS\saidas\ ;
  * anexa o PDF ao Processo, grava área livre, município da malha do IAT,
    status "Gerado" e a impressão digital dos dados.

Não há mais dados digitados neste arquivo: para corrigir algo no mapa,
corrija no Odoo e rode de novo.

Funciona no QGIS 3.34+ e no QGIS 4.x (Console Python: Complementos >
Console Python, ou Ctrl+Alt+P).

Pré-requisitos (uma vez só):
  * odoo_mapa.py e gerar_mapa_situacao.py na mesma pasta deste arquivo;
  * credenciais: rode uma vez o configurar_credenciais.py (cria o
    arquivo %USERPROFILE%\.gp_odoo.json e testa o login).
"""

# ===========================================================================
# O QUE MUDA A CADA EXECUÇÃO
# ===========================================================================
PROCESSO_ID = 66

# True = só confere os dados e mostra o que falta, sem gerar nem gravar nada.
SOMENTE_CONFERIR = False

# ===========================================================================
# CONFIGURAÇÃO (raramente muda)
# ===========================================================================
PASTA_GIS = r"D:\Guilherme\Guilherme\Documentos\Dados GIS"

# Metros de respiro entre a divisa do imóvel e a borda do mapa. A escala final
# sempre sobe para o próximo degrau redondo (1:6.000, 1:7.000...).
MARGEM_M = 400

# Pontos de referência ao vivo no OpenStreetMap (precisa de internet).
OSM_REFERENCIAS = True

# False = gera o PDF mas não grava nada no Odoo (útil para testar layout).
GRAVAR_NO_ODOO = True

# Modelo .qpt do layout para ajustes no Compositor. None = não gera.
ARQUIVO_QPT = None

# ===========================================================================
# Daqui para baixo não precisa mexer.
# ===========================================================================
import importlib
import sys


def _rodar():
    if PASTA_GIS not in sys.path:
        sys.path.insert(0, PASTA_GIS)
    import odoo_mapa
    importlib.reload(odoo_mapa)  # pega o arquivo salvo agora

    argv_original = sys.argv
    sys.argv = ["rodar_mapa.py"]  # não deixa argumento velho do Console vazar
    try:
        odoo_mapa.processar_processo(
            PROCESSO_ID, PASTA_GIS,
            margem_m=MARGEM_M, osm=OSM_REFERENCIAS, qpt=ARQUIVO_QPT,
            gravar_no_odoo=GRAVAR_NO_ODOO, somente_conferir=SOMENTE_CONFERIR,
        )
    except odoo_mapa.ErroOdoo as e:
        print(f"[ERRO] {e}")
    finally:
        sys.argv = argv_original


_rodar()
