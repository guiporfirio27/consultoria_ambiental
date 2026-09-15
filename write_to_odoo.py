"""
Grava no Odoo os dados extraídos dos documentos já processados
(lê a pasta processados/, gerada por process_document.py).

Não usa o conector MCP "Odoo MPC GP Construtora" -- esse só existe dentro de
uma sessão de chat do Claude. Conexão direta via XML-RPC.

Duas rotas de gravação, dependendo do tipo de documento:

  PESSOA (RG, CNH, CPF) -> res.partner
    Continua exigindo achar o cliente certo (por partner_id já resolvido
    a montante, ou por CPF já cadastrado). Sem cliente, vai para
    sem_correspondencia/.

  IMÓVEL (MAT-IMV, CAR, CADPRO) -> x_imovel
    NÃO depende de achar o cliente primeiro. Busca ou cria o registro de
    Imóvel pelo número do próprio documento (matrícula/CAR/protocolo CADPRO
    já são identificadores únicos) -- essencial porque um mesmo cliente
    pode ter mais de um imóvel em processos concorrentes, e o CPF sozinho
    não diferencia qual imóvel um documento novo pertence.

  COMP-RES continua fora do escopo deste script -- endereço já é tratado
  pelo pipeline existente de OCR de comprovante de residência.

Variáveis de ambiente esperadas:
  ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
"""

import json
import os
import xmlrpc.client
from pathlib import Path

PROCESSADOS_DIR = Path("./processados")
SEM_CORRESPONDENCIA_DIR = Path("./sem_correspondencia")
COMPANY_ID = 2  # GP Consultoria Ambiental

url = os.environ["ODOO_URL"]
db = os.environ["ODOO_DB"]
email = os.environ["ODOO_EMAIL"]
api_key = os.environ["ODOO_API_KEY"]

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, email, api_key, {})
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

CONTEXTO_EMPRESA = {"allowed_company_ids": [COMPANY_ID]}

CAMPO_POR_TIPO_PESSOA = {
    "numero_rg": "x_cliente_rg_numero",
    "orgao_emissor": "x_cliente_rg_orgao_emissor",
}

CONFIG_IMOVEL_POR_TIPO = {
    "MAT-IMV": {
        "campo_chave_json": "numero_matricula",
        "campo_chave_odoo": "x_numero_matricula",
        "outros_campos": {"cartorio": "x_cartorio"},
    },
    "CAR": {
        "campo_chave_json": "numero_car",
        "campo_chave_odoo": "x_numero_car",
        "outros_campos": {"area_total_ha": "x_car_area_total_ha"},
    },
    "CADPRO": {
        "campo_chave_json": "numero_protocolo",
        "campo_chave_odoo": "x_numero_cadpro_protocolo",
        "outros_campos": {},
    },
}


def buscar_partner_por_cpf(cpf: str):
    if not cpf:
        return None
    ids = models.execute_kw(
        db, uid, api_key, "res.partner", "search",
        [[["vat", "=", cpf], ["company_id", "=", COMPANY_ID]]],
        {"context": CONTEXTO_EMPRESA},
    )
    return ids[0] if ids else None


def encontrar_arquivo_original(caminho_json: Path):
    """O .json e o arquivo original compartilham o mesmo nome-base (stem);
    só muda a extensão. Acha o irmão binário do .json."""
    for candidato in PROCESSADOS_DIR.glob(f"{caminho_json.stem}.*"):
        if candidato.suffix != ".json":
            return candidato
    return None


def anexar_arquivo(caminho_arquivo: Path, res_model: str, res_id: int):
    """Anexa o documento original no registro certo do Odoo (ir.attachment) --
    é isso que vira o 'banco de documentos' navegável, e o que faz esse
    arquivo entrar automaticamente no backup diário que o Odoo já mantém."""
    if not caminho_arquivo or not caminho_arquivo.exists():
        return
    import base64
    conteudo_b64 = base64.b64encode(caminho_arquivo.read_bytes()).decode()
    models.execute_kw(
        db, uid, api_key, "ir.attachment", "create",
        [{
            "name": caminho_arquivo.name,
            "datas": conteudo_b64,
            "res_model": res_model,
            "res_id": res_id,
        }],
    )


def gravar_pessoa(resultado: dict, partner_id: int):
    tipo = resultado["tipo_documento"]
    campos = resultado["campos_extraidos"] or {}

    if tipo in ("CPF", "CNH"):
        numero_cpf = campos.get("numero_cpf")
        if numero_cpf:
            tipo_cpf_id = models.execute_kw(
                db, uid, api_key, "l10n_latam.identification.type", "search",
                [[["name", "=", "CPF"]]],
            )
            valores_cpf = {"vat": numero_cpf}
            if tipo_cpf_id:
                valores_cpf["l10n_latam_identification_type_id"] = tipo_cpf_id[0]
            models.execute_kw(
                db, uid, api_key, "res.partner", "write", [[partner_id], valores_cpf],
                {"context": CONTEXTO_EMPRESA},
            )
        if tipo == "CPF":
            return

    valores = {
        campo_odoo: campos[campo_json]
        for campo_json, campo_odoo in CAMPO_POR_TIPO_PESSOA.items()
        if campos.get(campo_json) is not None
    }
    if valores:
        models.execute_kw(
            db, uid, api_key, "res.partner", "write", [[partner_id], valores],
            {"context": CONTEXTO_EMPRESA},
        )


def gravar_imovel(resultado: dict):
    """Retorna o id do x_imovel gravado, ou None se não havia número
    identificador suficiente para prosseguir."""
    tipo = resultado["tipo_documento"]
    campos = resultado["campos_extraidos"] or {}
    config = CONFIG_IMOVEL_POR_TIPO[tipo]

    numero_chave = campos.get(config["campo_chave_json"])
    if not numero_chave:
        return None

    ids = models.execute_kw(
        db, uid, api_key, "x_imovel", "search",
        [[[config["campo_chave_odoo"], "=", numero_chave]]],
    )

    valores = {
        campo_odoo: campos[campo_json]
        for campo_json, campo_odoo in config["outros_campos"].items()
        if campos.get(campo_json) is not None
    }
    valores[config["campo_chave_odoo"]] = numero_chave

    if ids:
        models.execute_kw(db, uid, api_key, "x_imovel", "write", [[ids[0]], valores])
        return ids[0]
    else:
        valores["x_name"] = numero_chave
        novo_id = models.execute_kw(db, uid, api_key, "x_imovel", "create", [valores])
        return novo_id


def main():
    SEM_CORRESPONDENCIA_DIR.mkdir(exist_ok=True)
    for arquivo_json in sorted(PROCESSADOS_DIR.glob("*.json")):
        resultado = json.loads(arquivo_json.read_text())
        tipo = resultado["tipo_documento"]
        arquivo_original = encontrar_arquivo_original(arquivo_json)

        if tipo in CONFIG_IMOVEL_POR_TIPO:
            imovel_id = gravar_imovel(resultado)
            if imovel_id is None:
                arquivo_json.replace(SEM_CORRESPONDENCIA_DIR / arquivo_json.name)
                continue
            anexar_arquivo(arquivo_original, "x_imovel", imovel_id)
            continue

        campos = resultado.get("campos_extraidos") or {}
        partner_id = resultado.get("partner_id")
        if not partner_id:
            partner_id = buscar_partner_por_cpf(campos.get("numero_cpf"))

        if not partner_id:
            arquivo_json.replace(SEM_CORRESPONDENCIA_DIR / arquivo_json.name)
            continue

        gravar_pessoa(resultado, partner_id)
        anexar_arquivo(arquivo_original, "res.partner", partner_id)


if __name__ == "__main__":
    main()
