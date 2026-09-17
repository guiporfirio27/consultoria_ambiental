"""
criar_cliente_pendente.py

Substitui o comportamento de 'sem_correspondencia/' no pipeline de
GP Consultoria Ambiental. Onde antes o write_to_odoo.py desistia e
deixava o caso só como artefato do GitHub Actions (perdido em 7 dias),
agora ele:

  1. Busca o res.partner por CPF (campo nativo `vat`), escopado a
     company_id=2 -- igual ao que já era feito antes de decidir
     "sem correspondência".
  2. Se não achar, CRIA o res.partner mesmo assim, com
     x_status_cadastro = 'pendente_confirmacao', já com RG/CNH/CPF
     preenchidos a partir da extração.
  3. Anexa o documento original via ir.attachment (mesmo padrão já
     usado para os casos com correspondência).
  4. Notifica o Telegram pedindo confirmação humana do papel da
     pessoa (Cliente / Proprietário / Empreendedor / Terceiro) --
     o pipeline NUNCA decide isso sozinho, só cadastra os dados.

Integração: chame `buscar_ou_criar_pessoa(...)` no lugar do trecho de
write_to_odoo.py que hoje, ao não achar correspondência, grava o JSON
em sem_correspondencia/ e não escreve nada no Odoo. Ajuste os nomes de
chave do dicionário `dados_extraidos` conforme o schema real que o seu
process_document.py já produz (aqui assumo nome/cpf/rg_numero/
rg_orgao_emissor/tipo_documento com base no manual do pipeline).
"""

import os
import base64
import requests
import xmlrpc.client

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_EMAIL = os.environ["ODOO_EMAIL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
COMPANY_ID = 2  # GP Consultoria Ambiental -- nunca mudar sem revisar o resto do pipeline

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def _conectar_odoo():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_EMAIL, ODOO_API_KEY, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models


def _executar(models, uid, modelo, metodo, *args, **kwargs):
    kwargs.setdefault("context", {})["allowed_company_ids"] = [COMPANY_ID]
    return models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY, modelo, metodo, list(args), kwargs
    )


def _id_tipo_identificacao_cpf(models, uid):
    """Busca o id do tipo de identificação 'CPF' (módulo l10n_br)."""
    ids = _executar(
        models, uid, "l10n_latam.identification.type", "search",
        [["name", "=", "CPF"]],
    )
    if not ids:
        raise RuntimeError(
            "Tipo de identificação 'CPF' não encontrado em "
            "l10n_latam.identification.type -- confira se o módulo "
            "brasileiro está instalado nessa base."
        )
    return ids[0]


def buscar_ou_criar_pessoa(dados_extraidos: dict, caminho_arquivo_original: str) -> dict:
    """
    dados_extraidos deve conter pelo menos:
        - 'cpf'                (string, só dígitos ou formatado -- ajuste
                                 se seu process_document.py já normaliza)
        - 'nome'
        - 'tipo_documento'      ('RG' | 'CNH' | 'CPF')
        - 'rg_numero'           (opcional, RG/CNH)
        - 'rg_orgao_emissor'    (opcional, RG/CNH)

    Retorna um dict {'partner_id': int, 'criado': bool} para o
    write_to_odoo.py decidir o que gravar no JSON de saída e em qual
    pasta local (sugestão: renomear sem_correspondencia/ para
    criado_pendente/ -- ver marcar_drive_concluido.py).
    """
    uid, models = _conectar_odoo()

    cpf = dados_extraidos["cpf"]
    partner_ids = _executar(
        models, uid, "res.partner", "search",
        [["company_id", "=", COMPANY_ID], ["vat", "=", cpf]],
    )

    if partner_ids:
        partner_id = partner_ids[0]
        criado = False
    else:
        valores = {
            "name": dados_extraidos["nome"],
            "vat": cpf,
            "l10n_latam_identification_type_id": _id_tipo_identificacao_cpf(models, uid),
            "company_id": COMPANY_ID,
            "x_status_cadastro": "pendente_confirmacao",
        }
        if dados_extraidos.get("rg_numero"):
            valores["x_cliente_rg_numero"] = dados_extraidos["rg_numero"]
        if dados_extraidos.get("rg_orgao_emissor"):
            valores["x_cliente_rg_orgao_emissor"] = dados_extraidos["rg_orgao_emissor"]

        partner_id = _executar(models, uid, "res.partner", "create", valores)
        criado = True

    # Anexa o documento original -- mesmo padrão já usado para os
    # casos com correspondência (banco de documentos + backup diário).
    with open(caminho_arquivo_original, "rb") as f:
        conteudo_b64 = base64.b64encode(f.read()).decode()

    _executar(
        models, uid, "ir.attachment", "create",
        {
            "name": os.path.basename(caminho_arquivo_original),
            "datas": conteudo_b64,
            "res_model": "res.partner",
            "res_id": partner_id,
        },
    )

    if criado:
        _notificar_telegram_pendente(dados_extraidos, partner_id)

    return {"partner_id": partner_id, "criado": criado}


def _notificar_telegram_pendente(dados_extraidos: dict, partner_id: int):
    link = f"{ODOO_URL}/odoo/contacts/{partner_id}"
    texto = (
        "🟡 *Novo cadastro pendente de confirmação*\n\n"
        f"Nome: {dados_extraidos['nome']}\n"
        f"CPF: {dados_extraidos['cpf']}\n"
        f"Origem: {dados_extraidos.get('tipo_documento', '?')}\n\n"
        "Não havia cliente cadastrado com esse CPF -- o registro foi "
        "criado automaticamente, mas ninguém confirmou ainda se essa "
        "pessoa é Cliente, Proprietário, Empreendedor ou apenas um "
        "terceiro citado em um documento (ex. cedente de terra).\n\n"
        f"Confirme aqui: {link}"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown"},
        timeout=15,
    )
