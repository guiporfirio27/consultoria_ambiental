# -*- coding: utf-8 -*-
r"""
fila_mapas.py — gera, sem abrir o QGIS, todos os mapas que o Odoo pôs na fila.

O Odoo decide QUANDO (status "Na fila": anamnese concluída + CPF/CNPJ válido +
CAR válido); este script só EXECUTA. Processo sem nada na fila: termina em
segundos, sem gerar nada.

COMO RODAR À MÃO (Prompt de Comando do Windows):

    "C:\Program Files\QGIS 4.2\bin\python-qgis.bat" "<pasta dos scripts>\fila_mapas.py"

<pasta dos scripts> = onde estão fila_mapas.py, odoo_mapa.py e
gerar_mapa_situacao.py (ex.: C:\Users\<você>\Desktop\Qgis). Os .gpkg ficam
em PASTA_GIS (abaixo). Ajuste a pasta do QGIS se a instalação tiver outro nome.

COMO DEIXAR AUTOMÁTICO (Agendador de Tarefas do Windows):
  1. Agendador de Tarefas > Criar Tarefa... (não a "Básica") > Geral: nome
     "Fila de mapas GP", "Executar somente quando o usuário estiver conectado".
  2. Disparadores > Novo: Diariamente, "Repetir a tarefa a cada 30 minutos"
     por "Indefinidamente". Outro disparador: "Ao fazer logon".
  3. Ações > Nova > Iniciar um programa:
       Programa: "C:\Program Files\QGIS 4.2\bin\python-qgis.bat"
       Argumentos: "<pasta dos scripts>\fila_mapas.py"
  4. Condições: desmarque "somente se estiver ligado à energia CA" (notebook).
     Configurações: "Se a tarefa já estiver em execução: Não iniciar uma nova".

HD externo desconectado: a rodada não mexe em nada (os processos continuam
"Na fila") e anota no log; o mapa sai na primeira rodada com o HD ligado.

O resultado de cada rodada fica em  Dados GIS\saidas\fila_mapas.log  (sem o HD:
na pasta saidas ao lado do script).
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

# Pasta com os .gpkg (no HD externo). Os scripts (este, odoo_mapa.py e
# gerar_mapa_situacao.py) podem ficar em outra pasta, desde que juntos.
PASTA_GIS = r"D:\Guilherme\Guilherme\Documentos\Dados GIS"
MARGEM_M = 400
OSM_REFERENCIAS = True


def _log(texto):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {texto}"
    print(linha)
    try:
        base = Path(PASTA_GIS)
        if not base.exists():  # HD desconectado: log ao lado do script
            base = Path(__file__).resolve().parent
        arq = base / "saidas" / "fila_mapas.log"
        arq.parent.mkdir(parents=True, exist_ok=True)
        with open(arq, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def main():
    pasta_scripts = str(Path(__file__).resolve().parent)
    # A pasta dos scripts vem primeiro: uma cópia antiga em Dados GIS não pode
    # ser importada no lugar da atual.
    for p in (PASTA_GIS, pasta_scripts):
        if p not in sys.path:
            sys.path.insert(0, p)
    import odoo_mapa

    try:
        odoo = odoo_mapa.conectar()
    except Exception as e:  # sem internet ou credencial: tenta na próxima rodada
        _log(f"[ERRO] não conectou ao Odoo: {e}")
        return 1

    ids = odoo.call("project.project", "search",
                    [["company_id", "=", odoo_mapa.COMPANY_ID],
                     ["x_mapa_status", "=", "na_fila"]])
    if not ids:
        _log("fila vazia")
        return 0

    # Base GIS fora do ar (HD externo desconectado): não mexe em nada. Os
    # processos continuam "Na fila" e saem na primeira rodada com o HD ligado,
    # em vez de irem para "Erro" por falta do arquivo do CAR.
    car = Path(PASTA_GIS) / "imoveis_pr_poligonos_car.gpkg"
    if not car.exists():
        _log(f"[aguardando] base GIS não encontrada ({car}) — HD desconectado? "
             f"{len(ids)} processo(s) continuam na fila: {ids}")
        return 0

    _log(f"{len(ids)} processo(s) na fila: {ids}")

    # Uma única QgsApplication para a rodada inteira: o gerador detecta que já
    # existe uma e não abre/fecha a própria a cada mapa.
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    try:
        for pid in ids:
            try:
                pdf = odoo_mapa.processar_processo(
                    pid, PASTA_GIS, margem_m=MARGEM_M, osm=OSM_REFERENCIAS, odoo=odoo)
                _log(f"processo {pid}: {'gerado ' + str(pdf) if pdf else 'não gerado (ver histórico no Odoo)'}")
            except Exception as e:
                _log(f"[ERRO] processo {pid}: {e}")
                traceback.print_exc()
                try:
                    odoo_mapa.registrar_erro(odoo, pid, f"{type(e).__name__}: {e}")
                except Exception:
                    pass
    finally:
        app.exitQgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
