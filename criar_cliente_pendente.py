"""
criar_cliente_pendente.py

Cria no Odoo a pessoa citada num documento quando ela ainda não existe na
base, sem assumir qual é o papel dela. O pipeline NUNCA decide se alguém é
Cliente, Proprietário, Empreendedor ou Terceiro -- só cadastra os dados e
marca x_status_cadastro='pendente_confirmacao' para confirmação humana.

Usado em dois momentos:
  1. Documento de pessoa (RG/CNH/CPF) cujo CPF não corresponde a ninguém.
  2. Titular declarado num documento de imóvel (CAR/MAT-IMV/CADPRO) --
     caso em que o papel é ainda mais incerto (o titular de um CAR pode ser
     arrendatário, não proprietário), por isso o vínculo vai para o campo
     neutro x_titular_documento_id, nunca para x_proprietario_id.

MUDANÇAS DESTA VERSÃO (correção dos defeitos D1 e D6 da auditoria):

  D1 — ir.attachment era criado com o campo "datas", que NÃO EXISTE nesta
       instância Odoo (saas-19.3). O create() não dava erro: ignorava o campo
       em silêncio e gravava o anexo com 0 bytes. O campo correto é
       "db_datas". Nunca voltar a escrever "datas" em nenhum script desta
       base.

  D6 — nada impedia anexo duplicado. Agora o checksum (sha1) é verificado
       contra os anexos já existentes no mesmo registro antes de criar.

Variáveis de ambiente esperadas:
  ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (opcionais -- lidos sob demanda)
"""

import base64
import hashlib
import os
import re
import xmlrpc.client

import requests

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_EMAIL = os.environ["ODOO_EMAIL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
COMPANY_ID = 2  # GP Consultoria Ambiental -- nunca mudar sem revisar o resto do pipeline

# TELEGRAM_TOKEN/TELEGRAM_CHAT_ID são lidos sob demanda em
# _notificar_telegram_pendente(), não no nível do módulo -- ler com
# os.environ[...] aqui quebraria o import inteiro (e, com ele, toda a
# gravação no Odoo) se esse passo do workflow ficar sem os secrets.
# Já aconteceu uma vez.


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


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _id_tipo_identificacao(models, uid, documento: str) -> int | None:
    """CPF (11 dígitos) ou CNPJ (14 dígitos) -- o titular de um CAR ou de uma
    matrícula pode perfeitamente ser pessoa jurídica."""
    nome_tipo = "CNPJ" if len(so_digitos(documento)) == 14 else "CPF"
    ids = _executar(
        models, uid, "l10n_latam.identification.type", "search",
        [["name", "=", nome_tipo]],
    )
    return ids[0] if ids else None


PASTAS_POR_MODELO = {
    "res.partner": "gp.documentos.pasta_pessoas",
    "x_imovel": "gp.documentos.pasta_imoveis",
    "x_documento_pendente": "gp.documentos.pasta_pendentes",
}


def _pasta_documentos(models, uid, res_model: str):
    """Id da pasta do app Documentos para este tipo de registro.

    Guardado em ir.config_parameter, não fixado no código: assim você pode
    renomear ou remanejar as pastas no Odoo sem precisar de um commit."""
    chave = PASTAS_POR_MODELO.get(res_model)
    if not chave:
        return None
    registros = _executar(
        models, uid, "ir.config_parameter", "search_read",
        [["key", "=", chave]], fields=["value"], limit=1)
    if not registros:
        return None
    valor = (registros[0].get("value") or "").strip()
    return int(valor) if valor.isdigit() else None


def _fichar_no_app_documentos(models, uid, anexo_id: int, nome: str,
                              res_model: str, res_id: int, partner_id=None):
    """Cria o registro do app Documentos apontando para o anexo já existente.

    Anexo e app Documentos são coisas diferentes no Odoo: o `ir.attachment`
    aparece na área de anexos do registro, mas o botão "Documents" do contato
    e a árvore de pastas leem `documents.document`. Gravar só o anexo deixava
    o contador do botão em zero -- o arquivo existia e parecia não existir.

    Confirmado por teste direto: criar o `documents.document` apontando para um
    anexo já vinculado a um contato NÃO altera o `res_model`/`res_id` desse
    anexo, então o vínculo com o contato ou imóvel continua intacto."""
    pasta_id = _pasta_documentos(models, uid, res_model)
    if not pasta_id:
        print(f"[aviso] pasta do app Documentos não configurada para {res_model}")
        return None

    ja_existe = _executar(
        models, uid, "documents.document", "search",
        [["attachment_id", "=", anexo_id]], limit=1)

    valores = {"folder_id": pasta_id}
    if partner_id:
        valores["partner_id"] = partner_id

    if ja_existe:
        _executar(models, uid, "documents.document", "write", ja_existe, valores)
        print(f"[ok] ficha do app Documentos {ja_existe[0]} movida para a pasta {pasta_id}")
        return ja_existe[0]

    valores["name"] = nome
    valores["attachment_id"] = anexo_id
    ficha_id = _executar(models, uid, "documents.document", "create", valores)
    print(f"[ok] ficha {ficha_id} criada no app Documentos (pasta {pasta_id})")
    return ficha_id


def anexar_arquivo(models, uid, caminho_arquivo: str, res_model: str, res_id: int,
                   partner_id=None):
    """Anexa o documento original no registro certo (ir.attachment) E o ficha
    no app Documentos -- é isso que vira o banco de documentos navegável e o
    que faz o arquivo entrar no backup diário que o Odoo já mantém.

    CAMPO CORRETO NESTA BASE: db_datas. O campo 'datas' não existe aqui e é
    ignorado silenciosamente no create(), produzindo anexo de 0 bytes (D1)."""
    if not caminho_arquivo or not os.path.exists(caminho_arquivo):
        print(f"[aviso] arquivo não encontrado para anexar: {caminho_arquivo}")
        return None

    with open(caminho_arquivo, "rb") as f:
        conteudo = f.read()
    checksum = hashlib.sha1(conteudo).hexdigest()
    nome_arquivo = os.path.basename(caminho_arquivo)

    # D6: não duplicar o mesmo arquivo no mesmo registro a cada reprocessamento.
    existentes = _executar(
        models, uid, "ir.attachment", "search",
        [
            ["res_model", "=", res_model],
            ["res_id", "=", res_id],
            ["checksum", "=", checksum],
        ],
    )
    if existentes:
        print(f"[ok] anexo já existe em {res_model}/{res_id} (checksum {checksum[:8]})")
        # Mesmo já existindo, garante a ficha no app Documentos -- um anexo de
        # antes desta mudança não tem ficha nenhuma.
        _fichar_no_app_documentos(models, uid, existentes[0], nome_arquivo,
                                  res_model, res_id, partner_id)
        return existentes[0]

    anexo_id = _executar(
        models, uid, "ir.attachment", "create",
        {
            "name": nome_arquivo,
            "db_datas": base64.b64encode(conteudo).decode(),
            "res_model": res_model,
            "res_id": res_id,
        },
    )
    print(f"[ok] anexo criado id={anexo_id} em {res_model}/{res_id} ({len(conteudo)} bytes)")

    _fichar_no_app_documentos(models, uid, anexo_id, nome_arquivo,
                              res_model, res_id, partner_id)
    return anexo_id


def buscar_ou_criar_pessoa(
    dados_extraidos: dict,
    caminho_arquivo_original: str,
    origem: str = "documento de pessoa",
) -> dict:
    """
    dados_extraidos deve conter pelo menos:
        - 'numero_cpf'      CPF ou CNPJ (string)
        - 'nome_completo'
        - 'tipo_documento'  ('RG' | 'CNH' | 'CPF' | 'CAR' | 'MAT-IMV' | 'CADPRO')
        - 'numero_rg', 'orgao_emissor' (opcionais)

    Retorna {'partner_id': int, 'criado': bool}.
    """
    uid, models = _conectar_odoo()

    documento = dados_extraidos.get("numero_cpf")
    if not documento:
        raise ValueError("buscar_ou_criar_pessoa exige CPF/CNPJ -- chave 'numero_cpf'")

    # Busca tolerante a formatação: a base pode ter o CPF com ou sem pontuação.
    partner_ids = _executar(
        models, uid, "res.partner", "search",
        [["company_id", "=", COMPANY_ID], ["vat", "in", [documento, so_digitos(documento)]]],
    )

    if partner_ids:
        partner_id = partner_ids[0]
        criado = False
    else:
        valores = {
            "name": dados_extraidos.get("nome_completo") or f"(sem nome) {documento}",
            "vat": documento,
            "company_id": COMPANY_ID,
            "x_status_cadastro": "pendente_confirmacao",
        }
        tipo_id = _id_tipo_identificacao(models, uid, documento)
        if tipo_id:
            valores["l10n_latam_identification_type_id"] = tipo_id
        if dados_extraidos.get("numero_rg"):
            valores["x_cliente_rg_numero"] = dados_extraidos["numero_rg"]
        if dados_extraidos.get("orgao_emissor"):
            valores["x_cliente_rg_orgao_emissor"] = dados_extraidos["orgao_emissor"]

        partner_id = _executar(models, uid, "res.partner", "create", valores)

        # Nesta base, gravar `vat` no mesmo create() força is_company=True --
        # mesmo passando is_company=False junto, o valor é ignorado. Resultado:
        # toda pessoa física nascia marcada como Empresa, em silêncio.
        # Confirmado por teste direto: um write SEPARADO, depois do create,
        # é o único jeito de o valor pegar. Escrever `vat` num write também
        # dispara a mesma virada, então a correção vem sempre por último.
        eh_empresa = len(so_digitos(documento)) == 14
        _executar(models, uid, "res.partner", "write",
                  [partner_id], {"is_company": eh_empresa})
        criado = True

    anexar_arquivo(models, uid, caminho_arquivo_original, "res.partner", partner_id,
                   partner_id=partner_id)

    if criado:
        _notificar_telegram_pendente(dados_extraidos, partner_id, origem)

    return {"partner_id": partner_id, "criado": criado}


def _notificar_telegram_pendente(dados_extraidos: dict, partner_id: int, origem: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    link = f"{ODOO_URL}/odoo/contacts/{partner_id}"
    texto = (
        "🟡 *Novo cadastro pendente de confirmação*\n\n"
        f"Nome: {dados_extraidos.get('nome_completo', '?')}\n"
        f"CPF/CNPJ: {dados_extraidos.get('numero_cpf', '?')}\n"
        f"Origem: {dados_extraidos.get('tipo_documento', '?')} ({origem})\n\n"
        "Ninguém confirmou ainda se essa pessoa é Cliente, Proprietário, "
        "Empreendedor ou apenas um terceiro citado no documento "
        "(ex. arrendatário titular do CAR, ou cedente de terra).\n\n"
        f"Confirme aqui: {link}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as erro:  # notificação nunca derruba a gravação
        print(f"[aviso] falha ao notificar Telegram: {erro}")
