"""
coletar_whatsapp.py -- coleta documentos que chegaram pelo WhatsApp do Odoo.

Por que este script existe
--------------------------
O modulo `whatsapp` do Odoo Enterprise ja esta instalado nesta base e fala a
Meta Cloud API nativamente. Quando um cliente manda uma foto do RG ou o PDF do
CAR pelo WhatsApp, o Odoo ja grava esse arquivo como `ir.attachment` num canal
de conversa que **sabe quem e o contato**. Ou seja: diferente do scanner, aqui
o dono do documento nunca foi desconhecido -- e so nao jogar fora essa
informacao.

Este script pega esses anexos, baixa para ./inbox/ e escreve ao lado um
arquivo de contexto (`<nome>.contexto.json`) dizendo de qual `res.partner` ele
veio. O `process_document.py` classifica normalmente, e o `write_to_odoo.py`
grava direto na pessoa certa, sem precisar adivinhar por CPF.

Idempotencia -- duas travas independentes
-----------------------------------------
1. Marca d'agua: o maior `mail.message` id ja coletado fica guardado no
   proprio Odoo (`ir.config_parameter` `gp.whatsapp.ultimo_message_id`), nao
   num arquivo local -- o runner do GitHub Actions e descartado a cada
   execucao e nao guarda estado entre rodadas.
2. Checksum: antes de baixar, verifica se aquele mesmo conteudo ja esta
   anexado em algum contato, imovel ou pendencia. Se ja estiver, pula. Isso
   protege mesmo se a marca d'agua for perdida ou zerada a mao.

A marca d'agua so avanca no fim, depois de tudo baixado.

Variaveis de ambiente:
  ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
"""

import base64
import json
import os
import re
from pathlib import Path

from criar_cliente_pendente import _conectar_odoo, _executar

INBOX_DIR = Path("./inbox")
PARAMETRO_MARCA = "gp.whatsapp.ultimo_message_id"
MAX_POR_EXECUCAO = 50

# Tipos que interessam. Sticker/audio/video nunca sao documento.
EXTENSOES_ACEITAS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff")

uid, models = _conectar_odoo()


def ler_marca():
    registros = _executar(
        models, uid, "ir.config_parameter", "search_read",
        [["key", "=", PARAMETRO_MARCA]], fields=["value"], limit=1)
    if not registros:
        return 0
    try:
        return int(registros[0]["value"])
    except (TypeError, ValueError):
        return 0


def gravar_marca(valor: int):
    ids = _executar(
        models, uid, "ir.config_parameter", "search",
        [["key", "=", PARAMETRO_MARCA]], limit=1)
    if ids:
        _executar(models, uid, "ir.config_parameter", "write", ids, {"value": str(valor)})
    else:
        _executar(models, uid, "ir.config_parameter", "create",
                  {"key": PARAMETRO_MARCA, "value": str(valor)})


def canais_whatsapp():
    """Canais de conversa do WhatsApp, com o contato de cada um."""
    return _executar(
        models, uid, "discuss.channel", "search_read",
        [["channel_type", "=", "whatsapp"]],
        fields=["id", "name", "whatsapp_partner_id"])


def ja_temos_esse_conteudo(checksum: str) -> bool:
    """Trava 2: o mesmo arquivo ja esta arquivado em algum lugar do fluxo?"""
    if not checksum:
        return False
    quantos = _executar(
        models, uid, "ir.attachment", "search_count",
        [["checksum", "=", checksum],
         ["res_model", "in", ["res.partner", "x_imovel", "x_documento_pendente"]]])
    return bool(quantos)


def nome_seguro(nome: str, anexo_id: int) -> str:
    """Nome de arquivo previsivel: o WhatsApp manda coisas como
    'image.jpg' para todo mundo, entao o id do anexo entra no nome para
    dois clientes diferentes nao se sobrescreverem na inbox."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", nome or "arquivo")
    raiz, ponto, extensao = base.rpartition(".")
    if not ponto:
        raiz, extensao = base, "bin"
    return f"wa{anexo_id}_{raiz[:40]}.{extensao.lower()}"


def main():
    INBOX_DIR.mkdir(exist_ok=True)

    canais = canais_whatsapp()
    if not canais:
        print("Nenhum canal de WhatsApp nesta base -- o numero ja foi conectado "
              "em Ajustes > WhatsApp? Nada a coletar.")
        return

    por_canal = {}
    for canal in canais:
        parceiro = canal.get("whatsapp_partner_id")
        por_canal[canal["id"]] = parceiro[0] if parceiro else None
    print(f"{len(canais)} canal(is) de WhatsApp encontrados.")

    marca = ler_marca()
    print(f"Ultima mensagem ja coletada: id {marca}")

    mensagens = _executar(
        models, uid, "mail.message", "search_read",
        [["model", "=", "discuss.channel"],
         ["res_id", "in", list(por_canal.keys())],
         ["id", ">", marca],
         ["attachment_ids", "!=", False]],
        fields=["id", "res_id", "author_id", "attachment_ids", "date"],
        order="id asc", limit=MAX_POR_EXECUCAO)

    if not mensagens:
        print("Nenhuma mensagem nova com anexo.")
        return
    print(f"{len(mensagens)} mensagem(ns) nova(s) com anexo.")

    maior_id = marca
    baixados = 0
    pulados = 0

    for mensagem in mensagens:
        # O contato do canal e mais confiavel que o autor da mensagem: o autor
        # pode ser voce mesmo, quando VOCE manda um documento no chat.
        partner_id = por_canal.get(mensagem["res_id"])
        if not partner_id and mensagem.get("author_id"):
            partner_id = mensagem["author_id"][0]

        anexos = _executar(
            models, uid, "ir.attachment", "read",
            mensagem["attachment_ids"],
            ["id", "name", "mimetype", "checksum", "file_size"])

        for anexo in anexos:
            nome = anexo.get("name") or ""
            extensao = ("." + nome.rsplit(".", 1)[-1].lower()) if "." in nome else ""
            if extensao not in EXTENSOES_ACEITAS:
                print(f"  pulado (nao e documento): {nome}")
                pulados += 1
                continue
            if not anexo.get("file_size"):
                print(f"  pulado (arquivo vazio): {nome}")
                pulados += 1
                continue
            if ja_temos_esse_conteudo(anexo.get("checksum")):
                print(f"  pulado (ja arquivado antes): {nome}")
                pulados += 1
                continue

            conteudo = _executar(
                models, uid, "ir.attachment", "read", [anexo["id"]], ["db_datas"])
            dados = conteudo[0].get("db_datas") if conteudo else None
            if not dados:
                print(f"  pulado (sem conteudo no anexo {anexo['id']}): {nome}")
                pulados += 1
                continue

            destino = INBOX_DIR / nome_seguro(nome, anexo["id"])
            destino.write_bytes(base64.b64decode(dados))

            # O arquivo de contexto e o ponto inteiro deste script: o documento
            # chega ja sabendo de quem e.
            contexto = {
                "origem": "whatsapp",
                "partner_id": partner_id,
                "mail_message_id": mensagem["id"],
                "ir_attachment_id": anexo["id"],
                "data_mensagem": mensagem.get("date"),
            }
            destino.with_name(destino.name + ".contexto.json").write_text(
                json.dumps(contexto, ensure_ascii=False, indent=2), encoding="utf-8")

            print(f"  baixado: {destino.name} (contato {partner_id})")
            baixados += 1

        if mensagem["id"] > maior_id:
            maior_id = mensagem["id"]

    # So avanca a marca depois que tudo foi para o disco.
    if maior_id > marca:
        gravar_marca(maior_id)
        print(f"Marca d'agua avancada para {maior_id}")

    print(f"Resumo: {baixados} baixado(s), {pulados} pulado(s).")


if __name__ == "__main__":
    main()
