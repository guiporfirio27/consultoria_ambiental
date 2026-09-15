"""
Baixa arquivos novos da pasta de inbox no Google Drive para ./inbox/, antes
de process_document.py rodar.

Depois de baixar com sucesso, move o arquivo (no Drive) para uma subpasta
"Processados" -- não apaga, mantém rastro do que já passou pelo pipeline e
evita reprocessar o mesmo arquivo na próxima execução.

Variável de ambiente esperada:
  GOOGLE_SERVICE_ACCOUNT_JSON -- conteúdo completo do arquivo .json da
  conta de serviço (não o caminho do arquivo, o conteúdo em si).
"""

import io
import json
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

PASTA_INBOX_ID = "1y3CL2y8m7xMb9I7ZPKT0ctVt0mDILzRR"
INBOX_LOCAL = Path("./inbox")
NOME_SUBPASTA_PROCESSADOS = "Processados"

credenciais = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=["https://www.googleapis.com/auth/drive"],
)
drive = build("drive", "v3", credentials=credenciais)


def obter_ou_criar_subpasta_processados() -> str:
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
    INBOX_LOCAL.mkdir(exist_ok=True)

    query = f"'{PASTA_INBOX_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    resultado = drive.files().list(q=query, fields="files(id, name)").execute()
    arquivos = resultado.get("files", [])

    if not arquivos:
        print("Nenhum arquivo novo na pasta do Drive.")
        return

    subpasta_processados_id = obter_ou_criar_subpasta_processados()

    for arquivo in arquivos:
        destino_local = INBOX_LOCAL / arquivo["name"]
        request = drive.files().get_media(fileId=arquivo["id"])
        with io.FileIO(destino_local, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            concluido = False
            while not concluido:
                _, concluido = downloader.next_chunk()

        # Move para Processados/ dentro do Drive -- não apaga, evita
        # reprocessar o mesmo arquivo na próxima execução do workflow.
        drive.files().update(
            fileId=arquivo["id"],
            addParents=subpasta_processados_id,
            removeParents=PASTA_INBOX_ID,
        ).execute()

        print(f"Baixado: {arquivo['name']}")


if __name__ == "__main__":
    main()
