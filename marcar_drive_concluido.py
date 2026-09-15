"""
Roda no final do workflow (sempre, mesmo se uma etapa anterior falhar) e
decide quais arquivos do Drive podem ser marcados como concluídos --
movidos para a subpasta "Processados" -- e quais devem continuar na pasta
de entrada para serem tentados de novo na próxima execução.

Um arquivo só é marcado como concluído se existe um .json correspondente em
processados/, revisao_manual/ ou sem_correspondencia/ -- ou seja, o
pipeline efetivamente terminou de lidar com ele, mesmo que o destino final
tenha sido uma fila de revisão humana em vez de gravação direta no Odoo. Se
nada disso existir -- por exemplo, porque o processo travou no meio do
arquivo -- ele fica no Drive para ser tentado de novo automaticamente na
próxima execução, em vez de ficar "perdido" numa pasta intermediária.

Variável de ambiente esperada:
  GOOGLE_SERVICE_ACCOUNT_JSON
"""

import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

PASTA_INBOX_ID = "1y3CL2y8m7xMb9I7ZPKT0ctVt0mDILzRR"
NOME_SUBPASTA_PROCESSADOS = "Processados"
MAPA_PATH = Path("./drive_mapa.json")

PASTAS_TERMINAIS = [
    Path("./processados"),
    Path("./revisao_manual"),
    Path("./sem_correspondencia"),
]


def documento_foi_tratado(nome_original: str) -> bool:
    caminho_esperado = str(Path("inbox") / nome_original)
    for pasta in PASTAS_TERMINAIS:
        if not pasta.exists():
            continue
        for arquivo_json in pasta.glob("*.json"):
            try:
                dados = json.loads(arquivo_json.read_text())
            except json.JSONDecodeError:
                continue
            if dados.get("arquivo_origem") == caminho_esperado:
                return True
    return False


def obter_ou_criar_subpasta_processados(drive) -> str:
    query = (
        f"'{PASTA_INBOX_ID}' in parents and name = '{NOME_SUBPASTA_PROCESSADOS}' "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    resultado = drive.files().list(q=query, fields="files(id)").execute()
    achados = resultado.get("files", [])
    if achados:
        return achados[0]["id"]
    nova = drive.files().create(
        body={
            "name": NOME_SUBPASTA_PROCESSADOS,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [PASTA_INBOX_ID],
        },
        fields="id",
    ).execute()
    return nova["id"]


def main():
    if not MAPA_PATH.exists():
        print("Nenhum mapa de download encontrado -- nada a fazer.")
        return

    mapa = json.loads(MAPA_PATH.read_text())
    if not mapa:
        print("Nenhum arquivo foi baixado nesta execução.")
        return

    credenciais = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    drive = build("drive", "v3", credentials=credenciais)
    subpasta_id = None

    for nome_original, drive_id in mapa.items():
        if documento_foi_tratado(nome_original):
            if subpasta_id is None:
                subpasta_id = obter_ou_criar_subpasta_processados(drive)
            drive.files().update(
                fileId=drive_id,
                addParents=subpasta_id,
                removeParents=PASTA_INBOX_ID,
            ).execute()
            print(f"Concluído, movido no Drive: {nome_original}")
        else:
            print(f"Não concluído nesta execução, fica para tentar de novo: {nome_original}")


if __name__ == "__main__":
    main()
