# -*- coding: utf-8 -*-
r"""
configurar_credenciais.py — cria o arquivo de credenciais do Odoo (uma vez só).

COMO USAR no Console Python do QGIS:

    exec(open(r"D:\Guilherme\Guilherme\Documentos\Dados GIS\configurar_credenciais.py", encoding="utf-8").read())

Abre uma janela pedindo a chave de API do Odoo (o texto fica oculto e NÃO vai
para o histórico do console), grava o arquivo  C:\Users\<você>\.gp_odoo.json
e testa a conexão na hora. Se o teste passar, o rodar_mapa.py já funciona.

Para trocar a chave depois, é só rodar de novo.
"""

URL = "https://gp-construcaoengenharia.odoo.com"
EMAIL = "guiporfirio27@gmail.com"
# Nome do banco. No Odoo online costuma ser o subdomínio da URL. Se o teste
# disser que o banco não existe, confira em odoo.com > Meus bancos de dados.
BANCO = "gp-construcaoengenharia"

import json
import xmlrpc.client
from pathlib import Path


def _pedir_chave():
    try:
        from qgis.PyQt.QtWidgets import QInputDialog, QLineEdit
        chave, ok = QInputDialog.getText(
            None, "Chave de API do Odoo",
            "Cole a chave de API (Odoo > Meu perfil > Segurança da conta):",
            QLineEdit.EchoMode.Password)
        return chave.strip() if ok else ""
    except ImportError:  # fora do QGIS
        import getpass
        return getpass.getpass("Chave de API do Odoo: ").strip()


def _configurar():
    chave = _pedir_chave()
    if not chave:
        print("[cancelado] Nenhuma chave informada; nada foi gravado.")
        return

    print(f"[info] Testando login em {URL} (banco '{BANCO}')...")
    try:
        comum = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
        uid = comum.authenticate(BANCO, EMAIL, chave, {})
    except Exception as e:
        texto = str(e)
        if "does not exist" in texto or "database" in texto.lower():
            print(f"[ERRO] O banco '{BANCO}' não foi encontrado. Confira o nome em "
                  "odoo.com > Meus bancos de dados, corrija BANCO neste arquivo e rode de novo.")
        else:
            print(f"[ERRO] Não consegui falar com o Odoo: {texto[:200]}")
        print("Nada foi gravado.")
        return

    if not uid:
        print("[ERRO] Login recusado: a chave ou o e-mail não conferem. Nada foi gravado.")
        return

    arquivo = Path.home() / ".gp_odoo.json"
    arquivo.write_text(json.dumps(
        {"url": URL, "db": BANCO, "email": EMAIL, "api_key": chave},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] Login aceito (usuário {uid}). Credenciais gravadas em {arquivo}")
    print("[ok] Pronto: agora é só rodar o rodar_mapa.py.")


_configurar()
