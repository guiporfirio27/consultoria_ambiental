# -*- coding: utf-8 -*-
r"""
fila_mapas.py — gera, sem abrir o QGIS, todos os mapas que o Odoo pôs na fila.

O Odoo decide QUANDO (status "Na fila": anamnese concluída + CPF/CNPJ válido +
CAR válido); este script só EXECUTA. Processo sem nada na fila: termina em
segundos, sem gerar nada.

COMO RODAR À MÃO (Prompt de Comando do Windows):

    "C:\Program Files\QGIS 4.2\bin\python-qgis.bat" "D:\Guilherme\Guilherme\Documentos\Dados GIS\fila_mapas.py"

(ajuste a pasta do QGIS se a sua instalação tiver outro nome)

COMO DEIXAR AUTOMÁTICO (Agendador de Tarefas do Windows):
  1. Agendador de Tarefas > Criar Tarefa Básica > nome "Fila de mapas GP".
  2. Disparador: Diariamente, repetir a cada 30 minutos (aba Disparadores >
     Editar > "Repetir a tarefa a cada 30 minutos" por "Indefinidamente").
     Acrescente também o disparador "Ao fazer logon".
  3. Ação: Iniciar um programa
       Programa: C:\Program Files\QGIS 4.2\bin\python-qgis.bat
       Argumentos: "D:\Guilherme\Guilherme\Documentos\Dados GIS\fila_mapas.py"
  4. Marque "Executar somente quando o usuário estiver conectado" (o disco D:
     e as credenciais em %USERPROFILE% precisam estar acessíveis).

O resultado de cada rodada fica em  Dados GIS\saidas\fila_mapas.log .
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

PASTA_GIS = r"D:\Guilherme\Guilherme\Documentos\Dados GIS"
MARGEM_M = 400
OSM_REFERENCIAS = True


def _log(texto):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {texto}"
    print(linha)
    try:
        arq = Path(PASTA_GIS) / "saidas" / "fila_mapas.log"
        arq.parent.mkdir(parents=True, exist_ok=True)
        with open(arq, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def main():
    if PASTA_GIS not in sys.path:
        sys.path.insert(0, PASTA_GIS)
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
