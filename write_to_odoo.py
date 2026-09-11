"""
Grava no Odoo os dados extraídos dos documentos já processados
(lê a pasta processados/, gerada por process_document.py).

Diferente de process_document.py, este script NÃO usa o conector MCP
"Odoo MPC GP Construtora" -- esse conector só existe dentro de uma sessão de
chat do Claude. Rodando em GitHub Actions, a conexão é direto via XML-RPC,
no mesmo padrão já usado nos outros módulos do projeto.

Variáveis de ambiente esperadas:
  ODOO_URL       -- ex.: https://gp-construcaoengenharia.odoo.com
  ODOO_DB        -- nome do banco
  ODOO_EMAIL     -- usuário técnico
  ODOO_API_KEY   -- chave de API desse usuário

LIMITAÇÃO CONHECIDA, documentada e não escondida:
  Associar um documento ao cliente certo requer saber de antemão quem é o
  cliente. Hoje isso só funciona em dois casos:
    1. O arquivo .json já vem com "partner_id" preenchido (etapa anterior
       -- anamnese ou casamento por telefone do WhatsApp -- já sabia quem
       era o cliente e gravou isso no metadado).
    2. O próprio documento é um CPF e o número extraído já bate com um
       res.partner existente.
  Fora desses dois casos, o documento cai em sem_correspondencia/ para
  decisão manual -- nunca é gravado com um "melhor palpite" de cliente.
"""

import json
import os
import xmlrpc.client
from pathlib import Path

PROCESSADOS_DIR = Path("./processados")
SEM_CORRESPONDENCIA_DIR = Path("./sem_correspondencia")
COMPANY_ID = 2  # GP Consultoria Ambiental

CAMPO_POR_TIPO = {
    "RG": {
        "numero_rg": "x_cliente_rg_numero",
        "orgao_emissor": "x_cliente_rg_orgao_emissor",
    },
    "MAT-IMV": {
        "numero_matricula": "x_empreendimento_matricula_numero",
        "cartorio": "x_empreendimento_matricula_cartorio",
    },
    "CAR": {
        "numero_car": "x_empreendimento_car_numero",
        "area_total_ha": "x_empreendimento_car_area_total_ha",
    },
    "CADPRO": {
        "numero_protocolo": "x_empreendimento_cadpro_numero_protocolo",
    },
    # CPF grava no campo nativo `vat`, tratado à parte em gravar().
    # COMP-RES não grava aqui -- endereço já é tratado pelo pipeline
    # existente de OCR de comprovante de residência
    # (endereco_requerente_completo), fora do escopo deste script.
}

url = os.environ["ODOO_URL"]
db = os.environ["ODOO_DB"]
email = os.environ["ODOO_EMAIL"]
api_key = os.environ["ODOO_API_KEY"]

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, email, api_key, {})
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def buscar_partner_por_cpf(cpf: str):
    if not cpf:
        return None
    ids = models.execute_kw(
        db, uid, api_key, "res.partner", "search",
        [[["vat", "=", cpf], ["company_id", "=", COMPANY_ID]]],
    )
    return ids[0] if ids else None


def gravar(resultado: dict, partner_id: int):
    tipo = resultado["tipo_documento"]
    campos = resultado["campos_extraidos"] or {}

    if tipo == "CPF":
        numero_cpf = campos.get("numero_cpf")
        if not numero_cpf:
            return
        tipo_cpf_id = models.execute_kw(
            db, uid, api_key, "l10n_latam.identification.type", "search",
            [[["name", "=", "CPF"]]],
        )
        valores = {"vat": numero_cpf}
        if tipo_cpf_id:
            valores["l10n_latam_identification_type_id"] = tipo_cpf_id[0]
        models.execute_kw(
            db, uid, api_key, "res.partner", "write", [[partner_id], valores]
        )
        return

    mapa = CAMPO_POR_TIPO.get(tipo, {})
    valores = {
        campo_odoo: campos[campo_json]
        for campo_json, campo_odoo in mapa.items()
        if campos.get(campo_json) is not None
    }
    if valores:
        models.execute_kw(
            db, uid, api_key, "res.partner", "write", [[partner_id], valores]
        )


def main():
    SEM_CORRESPONDENCIA_DIR.mkdir(exist_ok=True)
    for arquivo_json in sorted(PROCESSADOS_DIR.glob("*.json")):
        resultado = json.loads(arquivo_json.read_text())
        campos = resultado.get("campos_extraidos") or {}

        partner_id = resultado.get("partner_id")
        if not partner_id:
            partner_id = buscar_partner_por_cpf(campos.get("numero_cpf"))

        if not partner_id:
            destino = SEM_CORRESPONDENCIA_DIR / arquivo_json.name
            arquivo_json.replace(destino)
            continue

        gravar(resultado, partner_id)


if __name__ == "__main__":
    main()
