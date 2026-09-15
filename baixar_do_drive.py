"""
Baixa arquivos novos da pasta de inbox no Google Drive para ./inbox/, antes
de process_document.py rodar.

NÃO move nada no Drive aqui -- só baixa, e grava um mapa (nome do arquivo
local -> id do arquivo no Drive) em ./drive_mapa.json. Quem decide se um
arquivo pode ser marcado como concluído no Drive é o
marcar_drive_concluido.py, no final do workflow, depois de confirmar que o
pipeline inteiro terminou aquele arquivo -- assim, se qualquer etapa
seguinte falhar no meio do caminho, o arquivo continua na pasta de entrada
do Drive na próxima execução, em vez de ficar "perdido" numa pasta
intermediária sem nunca ter sido gravado no Odoo.

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
MAPA_PATH = Path("./drive_mapa.json")

credenciais = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
    scopes=["https://www.googleapis.com/auth/drive"],
)
drive = build("drive", "v3", credentials=credenciais)


def main():
    INBOX_LOCAL.mkdir(exist_ok=True)

    query = f"'{PASTA_INBOX_ID}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    resultado = drive.files().list(q=query, fields="files(id, name)").execute()
    arquivos = resultado.get("files", [])

    if not arquivos:
        print("Nenhum arquivo novo na pasta do Drive.")
        MAPA_PATH.write_text(json.dumps({}))
        return

    mapa = {}
    for arquivo in arquivos:
        destino_local = INBOX_LOCAL / arquivo["name"]
        request = drive.files().get_media(fileId=arquivo["id"])
        with io.FileIO(destino_local, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            concluido = False
            while not concluido:
                _, concluido = downloader.next_chunk()

        mapa[arquivo["name"]] = arquivo["id"]
        print(f"Baixado: {arquivo['name']}")

    MAPA_PATH.write_text(json.dumps(mapa, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
