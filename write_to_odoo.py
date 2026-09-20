"""
Grava no Odoo os dados extraídos dos documentos já processados.

Lê DUAS pastas, com destinos diferentes:

  processados/    -> grava direto nos campos definitivos (alta confiança)
  revisao_manual/ -> vira pendência no Odoo, para você confirmar em 2 cliques

Não usa o conector MCP "Odoo MPC GP Construtora" -- esse só existe dentro de
uma sessão de chat do Claude. Conexão direta via XML-RPC.

Rotas de gravação:

  PESSOA (RG, CNH, CPF) -> res.partner
    Se o documento chegou com contexto (WhatsApp, anamnese), o contato já é
    conhecido e nem se consulta CPF. Senão, busca por CPF; se não achar, cria
    marcado `pendente_confirmacao`, sem assumir papel.

  IMÓVEL (MAT-IMV, CAR, CADPRO) -> x_imovel
    Busca ou cria pelo número do próprio documento. O titular declarado vai
    para `x_titular_documento_id` (campo neutro), NUNCA `x_proprietario_id`:
    o titular de um CAR pode legitimamente ser arrendatário, e gravá-lo como
    proprietário seria um erro silencioso e frequente.

  COMP-RES e NAO_IDENTIFICADO -> fila de confirmação no Odoo.

O que mudou nesta versão
------------------------
1. `revisao_manual/` deixou de ser um ZIP no GitHub que expira em 7 dias e
   passou a ser o modelo `x_documento_pendente`, com o arquivo anexado. Nada
   mais se perde por prazo, e o tratamento acontece no Odoo, não no GitHub.
2. Quando a gravação direta não consegue resolver o destino (sem CPF, sem
   número de documento), em vez de mover o JSON para uma pasta que ninguém
   olha, o caso vai para a mesma fila, com o motivo escrito em português.
3. O contexto de origem (`partner_id` vindo do WhatsApp) é respeitado e tem
   prioridade sobre qualquer busca por CPF.

Variáveis de ambiente: ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
"""

import json
import os
import re
from pathlib import Path

from criar_cliente_pendente import (
    _conectar_odoo,
    _executar,
    anexar_arquivo,
    buscar_ou_criar_pessoa,
)
from fila_odoo import enfileirar, notificar_telegram_fila

PROCESSADOS_DIR = Path("./processados")
REVISAO_DIR = Path("./revisao_manual")

CAMPOS_PESSOA = {
    "numero_rg": "x_cliente_rg_numero",
    "orgao_emissor": "x_cliente_rg_orgao_emissor",
}

CONFIG_IMOVEL = {
    "MAT-IMV": {
        "campo_chave_json": "numero_matricula",
        "campo_chave_odoo": "x_numero_matricula",
        "texto": {
            "cartorio": "x_cartorio",
            "comarca": "x_comarca",
            "municipio": "x_municipio",
        },
        "numerico": {"area_ha": "x_area_ha"},
        "data": {},
    },
    "CAR": {
        "campo_chave_json": "numero_car",
        "campo_chave_odoo": "x_numero_car",
        "texto": {"municipio": "x_municipio"},
        "numerico": {"area_total_ha": "x_car_area_total_ha"},
        "data": {"data_emissao": "x_car_data_emissao"},
    },
    "CADPRO": {
        "campo_chave_json": "numero_protocolo",
        "campo_chave_odoo": "x_numero_cadpro_protocolo",
        "texto": {"municipio": "x_municipio"},
        "numerico": {},
        "data": {},
    },
}

TIPOS_PESSOA = ("RG", "CNH", "CPF")

uid, models = _conectar_odoo()
ODOO_URL = os.environ["ODOO_URL"]


def data_valida(valor) -> str | None:
    if isinstance(valor, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor.strip()):
        return valor.strip()
    return None


def numero_valido(valor) -> float | None:
    """Aceita número JSON direto (12.5) ou string em formato brasileiro
    ("1.234,56"). Cuidado: não normalizar um float como se fosse string --
    "12.5" com o ponto removido vira 125."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(" ", "")
    if "," in texto:  # formato brasileiro: ponto é milhar, vírgula é decimal
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def gravar_pessoa(resultado: dict, partner_id: int):
    campos = resultado.get("campos_extraidos") or {}
    valores = {}

    if campos.get("numero_cpf"):
        tipo_ids = _executar(
            models, uid, "l10n_latam.identification.type", "search",
            [["name", "=", "CPF"]],
        )
        valores["vat"] = campos["numero_cpf"]
        if tipo_ids:
            valores["l10n_latam_identification_type_id"] = tipo_ids[0]

    for chave_json, campo_odoo in CAMPOS_PESSOA.items():
        if campos.get(chave_json):
            valores[campo_odoo] = campos[chave_json]

    if valores:
        # Gravar `vat` vira is_company para True nesta base, inclusive em write
        # (ver comentário em criar_cliente_pendente.py). Guardamos o valor antes
        # e restauramos depois, para não transformar um cliente pessoa física
        # em Empresa só porque o CPF foi preenchido.
        antes = _executar(models, uid, "res.partner", "read",
                          [partner_id], ["is_company"])
        era_empresa = antes[0]["is_company"] if antes else False

        _executar(models, uid, "res.partner", "write", [partner_id], valores)
        print(f"[ok] res.partner {partner_id} atualizado: {list(valores)}")

        if "vat" in valores:
            depois = _executar(models, uid, "res.partner", "read",
                               [partner_id], ["is_company"])
            if depois and depois[0]["is_company"] != era_empresa:
                _executar(models, uid, "res.partner", "write",
                          [partner_id], {"is_company": era_empresa})
                print(f"[ok] is_company restaurado para {era_empresa}")


def vincular_titular(resultado: dict, imovel_id: int, caminho_arquivo: str):
    """Cadastra o titular declarado e o liga ao imóvel num campo NEUTRO.
    NUNCA em x_proprietario_id -- ver cabeçalho do módulo."""
    campos = resultado.get("campos_extraidos") or {}
    documento = campos.get("documento_titular")
    nome = campos.get("titular")

    if not documento:
        if nome:
            print(f"[aviso] titular '{nome}' extraído sem CPF/CNPJ -- "
                  "sem chave confiável para vincular, confirmação manual")
        return

    atual = _executar(
        models, uid, "x_imovel", "read", [imovel_id], ["x_titular_documento_id"])
    if atual and atual[0].get("x_titular_documento_id"):
        return  # já vinculado, não sobrescrever decisão humana

    pessoa = buscar_ou_criar_pessoa(
        {
            "numero_cpf": documento,
            "nome_completo": nome,
            "tipo_documento": resultado["tipo_documento"],
        },
        caminho_arquivo,
        origem="titular declarado em documento de imóvel",
    )
    _executar(
        models, uid, "x_imovel", "write", [imovel_id],
        {"x_titular_documento_id": pessoa["partner_id"]})
    print(f"[ok] titular {pessoa['partner_id']} vinculado ao imóvel {imovel_id}")


def gravar_imovel(resultado: dict) -> int | None:
    tipo = resultado["tipo_documento"]
    campos = resultado.get("campos_extraidos") or {}
    config = CONFIG_IMOVEL[tipo]

    # Contexto de origem tem prioridade: se já sabemos o imóvel, é ele.
    if resultado.get("imovel_id"):
        return resultado["imovel_id"]

    numero_chave = campos.get(config["campo_chave_json"])
    if not numero_chave:
        return None

    valores = {config["campo_chave_odoo"]: numero_chave}
    for chave_json, campo_odoo in config["texto"].items():
        if campos.get(chave_json):
            valores[campo_odoo] = campos[chave_json]
    for chave_json, campo_odoo in config["numerico"].items():
        numero = numero_valido(campos.get(chave_json))
        if numero is not None:
            valores[campo_odoo] = numero
    for chave_json, campo_odoo in config["data"].items():
        data = data_valida(campos.get(chave_json))
        if data:
            valores[campo_odoo] = data

    ids = _executar(
        models, uid, "x_imovel", "search",
        [[config["campo_chave_odoo"], "=", numero_chave]])

    if ids:
        _executar(models, uid, "x_imovel", "write", [ids[0]], valores)
        print(f"[ok] x_imovel {ids[0]} atualizado ({tipo} {numero_chave})")
        return ids[0]

    valores["x_name"] = numero_chave
    # create recebe o dict direto: passar [valores] devolveria uma LISTA de ids.
    novo_id = _executar(models, uid, "x_imovel", "create", valores)
    print(f"[ok] x_imovel {novo_id} criado ({tipo} {numero_chave})")
    return novo_id


def resolver_pessoa(resultado: dict) -> int | None:
    """Ordem de resolução: contexto de origem primeiro (WhatsApp já sabe de
    quem é), CPF depois. Nunca inventa."""
    if resultado.get("partner_id"):
        return resultado["partner_id"]

    campos = resultado.get("campos_extraidos") or {}
    cpf = campos.get("numero_cpf")
    if not cpf:
        return None

    encontrados = _executar(
        models, uid, "res.partner", "search",
        [["company_id", "=", 2], ["vat", "in", [cpf, so_digitos(cpf)]]])
    return encontrados[0] if encontrados else None


def arquivo_do_resultado(resultado: dict, caminho_json: Path) -> str | None:
    """O JSON carrega o caminho do binário -- não dependemos de adivinhar por
    nome, porque um arquivo pode gerar vários JSONs (um por tipo)."""
    caminho = resultado.get("arquivo_processado")
    if caminho and Path(caminho).exists():
        return caminho
    print(f"[aviso] binário não localizado para {caminho_json.name}")
    return None


def tratar_automatico(resultado: dict, caminho_arquivo: str) -> str | None:
    """Tenta gravar direto. Devolve None se conseguiu, ou o motivo pelo qual
    o caso precisa de confirmação humana."""
    tipo = resultado.get("tipo_documento")
    campos = resultado.get("campos_extraidos") or {}

    # ------------------------------------------------------------ IMÓVEL
    if tipo in CONFIG_IMOVEL:
        imovel_id = gravar_imovel(resultado)
        if imovel_id is None:
            return (f"Documento de imóvel sem o número identificador "
                    f"({CONFIG_IMOVEL[tipo]['campo_chave_json']}). "
                    "Preencha o número ou escolha o imóvel.")
        if caminho_arquivo:
            anexar_arquivo(models, uid, caminho_arquivo, "x_imovel", imovel_id)
            vincular_titular(resultado, imovel_id, caminho_arquivo)
        return None

    # ------------------------------------------------------------ PESSOA
    if tipo in TIPOS_PESSOA:
        partner_id = resolver_pessoa(resultado)

        if partner_id:
            gravar_pessoa(resultado, partner_id)
            if caminho_arquivo:
                anexar_arquivo(models, uid, caminho_arquivo, "res.partner", partner_id)
            return None

        if not campos.get("numero_cpf"):
            return ("Documento de pessoa sem CPF legível e sem contato de origem. "
                    "Escolha a pessoa ou preencha o CPF.")

        # Tem CPF mas ninguém cadastrado: cria pendente e grava.
        buscar_ou_criar_pessoa(
            {**campos, "tipo_documento": tipo},
            caminho_arquivo,
            origem="documento de pessoa sem cadastro correspondente")
        return None

    # -------------------------------------------- SEM ROTA AUTOMÁTICA
    if tipo == "COMP-RES":
        return ("Comprovante de residência: o endereço precisa de conferência "
                "antes de gravar. Escolha a pessoa e confirme.")

    return f"Tipo '{tipo}' não reconhecido com confiança suficiente."


def main():
    PROCESSADOS_DIR.mkdir(exist_ok=True)
    REVISAO_DIR.mkdir(exist_ok=True)
    enfileirados = 0

    # ---- 1. Alta confiança: tenta gravar direto -------------------------
    for arquivo_json in sorted(PROCESSADOS_DIR.glob("*.json")):
        resultado = json.loads(arquivo_json.read_text(encoding="utf-8"))
        caminho_arquivo = arquivo_do_resultado(resultado, arquivo_json)
        try:
            motivo = tratar_automatico(resultado, caminho_arquivo)
            if motivo:
                # Não deu para resolver sozinho -- vai para a fila do Odoo, com
                # o motivo em português, em vez de sumir numa pasta.
                if enfileirar(models, uid, resultado, caminho_arquivo, motivo,
                              partner_id=resultado.get("partner_id"),
                              imovel_id=resultado.get("imovel_id")):
                    enfileirados += 1
        except Exception as erro:
            print(f"[ERRO] {arquivo_json.name}: {erro}")
            try:
                if enfileirar(models, uid, resultado, caminho_arquivo,
                              f"Falha ao gravar automaticamente: {erro}",
                              partner_id=resultado.get("partner_id"),
                              imovel_id=resultado.get("imovel_id")):
                    enfileirados += 1
            except Exception as erro_fila:
                print(f"[ERRO] não consegui nem enfileirar: {erro_fila}")

    # ---- 2. Baixa confiança: direto para a fila de confirmação ----------
    for arquivo_json in sorted(REVISAO_DIR.glob("*.json")):
        resultado = json.loads(arquivo_json.read_text(encoding="utf-8"))
        caminho_arquivo = arquivo_do_resultado(resultado, arquivo_json)
        motivo = resultado.get("motivo") or "Confiança abaixo do limite."
        try:
            if enfileirar(models, uid, resultado, caminho_arquivo, motivo,
                          partner_id=resultado.get("partner_id"),
                          imovel_id=resultado.get("imovel_id")):
                enfileirados += 1
        except Exception as erro:
            print(f"[ERRO] ao enfileirar {arquivo_json.name}: {erro}")

    print(f"\nResumo: {enfileirados} documento(s) aguardando confirmação no Odoo.")
    notificar_telegram_fila(enfileirados, ODOO_URL)


if __name__ == "__main__":
    main()
