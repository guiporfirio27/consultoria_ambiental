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

Integração: chamado a partir de write_to_odoo.py, no trecho que hoje, ao não
achar correspondência de CPF, gravava o JSON em sem_correspondencia/ sem
escrever nada no Odoo. Nomes de chave já ajustados para o schema real
produzido por process_document.py (numero_cpf/nome_completo/numero_rg/
orgao_emissor/tipo_documento).
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

# TELEGRAM_TOKEN/TELEGRAM_CHAT_ID são lidos sob demanda em
# _notificar_telegram_pendente(), não aqui no nível do módulo -- este
# arquivo é importado por write_to_odoo.py, cujo passo no workflow só
# recebe as variáveis ODOO_*. Ler com os.environ[...] aqui quebraria o
# import inteiro (e, com ele, toda a gravação de pessoa no Odoo) se esse
# passo alguma vez ficar sem os secrets de Telegram -- já aconteceu uma
# vez. Sem o token, a notificação só é pulada, igual ao padrão já usado em
# process_document.py.


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
        - 'numero_cpf'          (string -- chave real produzida por
                                 process_document.py, ver
                                 prompts-classificacao-extracao-documentos.md)
        - 'nome_completo'
        - 'tipo_documento'      ('RG' | 'CNH' | 'CPF')
        - 'numero_rg'           (opcional, RG/CNH)
        - 'orgao_emissor'       (opcional, RG/CNH)

    Retorna um dict {'partner_id': int, 'criado': bool} para o
    write_to_odoo.py decidir o que gravar no JSON de saída e em qual
    pasta local (sugestão: renomear sem_correspondencia/ para
    criado_pendente/ -- ver marcar_drive_concluido.py).
    """
    uid, models = _conectar_odoo()

    cpf = dados_extraidos["numero_cpf"]
    partner_ids = _executar(
        models, uid, "res.partner", "search",
        [["company_id", "=", COMPANY_ID], ["vat", "=", cpf]],
    )

    if partner_ids:
        partner_id = partner_ids[0]
        criado = False
    else:
        valores = {
            "name": dados_extraidos["nome_completo"],
            "vat": cpf,
            "l10n_latam_identification_type_id": _id_tipo_identificacao_cpf(models, uid),
            "company_id": COMPANY_ID,
            "x_status_cadastro": "pendente_confirmacao",
        }
        if dados_extraidos.get("numero_rg"):
            valores["x_cliente_rg_numero"] = dados_extraidos["numero_rg"]
        if dados_extraidos.get("orgao_emissor"):
            valores["x_cliente_rg_orgao_emissor"] = dados_extraidos["orgao_emissor"]

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
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # Sem os secrets de Telegram disponíveis neste passo, só pula a
        # notificação -- o cadastro pendente já foi criado no Odoo, isso
        # não pode ser bloqueado por uma notificação opcional.
        return
    link = f"{ODOO_URL}/odoo/contacts/{partner_id}"
    texto = (
        "🟡 *Novo cadastro pendente de confirmação*\n\n"
        f"Nome: {dados_extraidos['nome_completo']}\n"
        f"CPF: {dados_extraidos['numero_cpf']}\n"
        f"Origem: {dados_extraidos.get('tipo_documento', '?')}\n\n"
        "Não havia cliente cadastrado com esse CPF -- o registro foi "
        "criado automaticamente, mas ninguém confirmou ainda se essa "
        "pessoa é Cliente, Proprietário, Empreendedor ou apenas um "
        "terceiro citado em um documento (ex. cedente de terra).\n\n"
        f"Confirme aqui: {link}"
    )
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
        timeout=15,
    )
