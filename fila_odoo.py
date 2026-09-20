"""
fila_odoo.py -- empurra um documento para a fila de confirmacao dentro do Odoo
(modelo `x_documento_pendente`).

Por que isso existe
-------------------
Antes, um documento que a IA nao conseguia gravar sozinha (confianca baixa,
CPF que nao bate, imovel sem numero) virava um arquivo dentro de um ZIP no
GitHub Actions, retido por 7 dias. Passado esse prazo, sumia. Na pratica isso
e perda de dado com hora marcada, e obriga voce a abrir o GitHub para tratar
um assunto que e do Odoo.

Agora vira um registro no Odoo, com o arquivo anexado, visivel no menu
"Documentos pendentes". Voce confere, corrige o que estiver errado e usa
Acoes > Confirmar e gravar. A acao de servidor grava nos campos definitivos e
move o anexo para o contato ou o imovel certo.

Os dados extraidos vao em formato "chave = valor", uma por linha -- e nao JSON
-- justamente porque esse campo existe para voce ler e corrigir na tela. Um
JSON com chaves e aspas e facil de quebrar ao editar a mao.

Variaveis de ambiente: ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
"""

import hashlib
import os

from criar_cliente_pendente import _executar, anexar_arquivo

# Rotulos legiveis para o campo de motivo
ORIGENS_VALIDAS = ("whatsapp", "drive", "anamnese", "pesquisa", "outro")

# Ordem de exibicao dos campos na tela -- os identificadores primeiro, porque
# sao eles que voce confere antes de confirmar.
ORDEM_CAMPOS = [
    "nome_completo", "numero_cpf", "numero_rg", "orgao_emissor",
    "numero_car", "numero_matricula", "numero_protocolo",
    "titular", "documento_titular",
    "municipio", "comarca", "cartorio", "uf",
    "area_ha", "area_total_ha", "data_emissao",
    "atividade_declarada", "data_nascimento", "numero_registro_cnh",
    "filiacao_mae", "filiacao_pai",
    "logradouro", "numero", "bairro", "cep", "tipo_comprovante",
]


def formatar_campos(campos: dict) -> str:
    """dict -> texto "chave = valor", uma por linha, em ordem estavel."""
    linhas = []
    vistos = set()
    for chave in ORDEM_CAMPOS:
        if chave in campos and campos[chave] not in (None, "", []):
            linhas.append(f"{chave} = {campos[chave]}")
            vistos.add(chave)
    for chave in sorted(campos):
        if chave in vistos or chave == "confianca":
            continue
        if campos[chave] in (None, "", []):
            continue
        linhas.append(f"{chave} = {campos[chave]}")
    return "\n".join(linhas)


def ja_esta_na_fila(models, uid, checksum: str):
    """Evita criar a mesma pendencia duas vezes se o workflow reprocessar."""
    if not checksum:
        return None
    ids = _executar(
        models, uid, "x_documento_pendente", "search",
        [["x_checksum", "=", checksum], ["x_status", "=", "aguardando"]], limit=1)
    return ids[0] if ids else None


def ja_foi_resolvido(models, uid, checksum: str) -> bool:
    """Se esse conteudo ja esta anexado num contato ou imovel, o documento ja
    foi tratado -- nao reabrir pendencia para ele."""
    if not checksum:
        return False
    quantos = _executar(
        models, uid, "ir.attachment", "search_count",
        [["checksum", "=", checksum],
         ["res_model", "in", ["res.partner", "x_imovel"]]])
    return bool(quantos)


def enfileirar(models, uid, resultado: dict, caminho_arquivo: str,
               motivo: str, partner_id=None, imovel_id=None) -> int | None:
    """Cria (ou reaproveita) a pendencia no Odoo e anexa o arquivo nela.
    Devolve o id da pendencia, ou None se nao havia nada a fazer."""
    campos = resultado.get("campos_extraidos") or {}
    checksum = resultado.get("checksum_sha1")

    if not checksum and caminho_arquivo and os.path.exists(caminho_arquivo):
        with open(caminho_arquivo, "rb") as f:
            checksum = hashlib.sha1(f.read()).hexdigest()

    if ja_foi_resolvido(models, uid, checksum):
        print(f"[ok] conteudo ja arquivado em contato/imovel -- nao enfileirado")
        return None

    existente = ja_esta_na_fila(models, uid, checksum)
    if existente:
        print(f"[ok] ja existe pendencia aguardando (id {existente}) -- nao duplicado")
        return existente

    origem = resultado.get("origem") or "drive"
    if origem not in ORIGENS_VALIDAS:
        origem = "outro"

    tipo = resultado.get("tipo_documento") or "NAO_IDENTIFICADO"
    nome_arquivo = os.path.basename(caminho_arquivo) if caminho_arquivo else "(sem arquivo)"

    valores = {
        "x_name": f"{tipo} - {nome_arquivo}",
        "x_arquivo_nome": nome_arquivo,
        "x_tipo_documento": tipo,
        "x_status": "aguardando",
        "x_origem": origem,
        "x_motivo": motivo[:250],
        "x_confianca": int(resultado.get("confianca_extracao") or 0),
        "x_dados_extraidos": formatar_campos(campos),
        "x_checksum": checksum or "",
    }
    if partner_id:
        valores["x_partner_id"] = partner_id
    if imovel_id:
        valores["x_imovel_id"] = imovel_id

    pendencia_id = _executar(models, uid, "x_documento_pendente", "create", valores)
    print(f"[ok] pendencia {pendencia_id} criada ({tipo}) -- motivo: {motivo}")

    if caminho_arquivo:
        anexar_arquivo(models, uid, caminho_arquivo,
                       "x_documento_pendente", pendencia_id)

    return pendencia_id


def notificar_telegram_fila(quantidade: int, url_odoo: str):
    """Um aviso por execucao, nao um por documento -- 12 documentos de uma
    digitalizacao em lote nao devem virar 12 mensagens no celular."""
    if not quantidade:
        return
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    import json
    import urllib.request

    plural = "documento aguarda" if quantidade == 1 else "documentos aguardam"
    texto = (f"📄 {quantidade} {plural} sua confirmacao no Odoo.\n\n"
             f"{url_odoo}/odoo/action-1422")
    dados = json.dumps({"chat_id": chat_id, "text": texto}).encode()
    pedido = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=dados, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(pedido, timeout=15)
    except Exception as erro:
        print(f"[aviso] falha ao notificar Telegram: {erro}")
