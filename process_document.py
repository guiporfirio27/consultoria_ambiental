"""
Pipeline de classificação e extração de documentos — GP Consultoria Ambiental.

Fluxo: arquivo (PDF ou imagem) -> páginas -> classificação -> extração ->
decisão (grava automaticamente / vai para revisão humana).

Prompts vivem em prompts-classificacao-extracao-documentos.md, não aqui.

Pré-requisitos de ambiente (variáveis já usadas nos outros módulos do projeto):
  ANTHROPIC_API_KEY   -- chamada à API do Claude
  TELEGRAM_TOKEN       -- notificação da fila de revisão
  TELEGRAM_CHAT_ID     -- para onde a notificação vai

Este script processa uma pasta local de entrada (./inbox). A etapa de baixar
os arquivos do Google Drive para essa pasta é um passo anterior e separado —
ver observações no final do arquivo.
"""

import base64
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

import anthropic
from PIL import Image
import io

MODEL = "claude-sonnet-5"
CONFIANCA_MINIMA = 70
LADO_MAXIMO_IMAGEM = 1568  # recomendação da Anthropic para imagens de visão
QUALIDADE_JPEG = 85

INBOX_DIR = Path("./inbox")
PROCESSADOS_DIR = Path("./processados")
REVISAO_DIR = Path("./revisao_manual")

PROMPT_CLASSIFICACAO = """Você vai receber a imagem de uma página de documento. Classifique-a em UM dos
seguintes tipos, baseado exclusivamente no conteúdo visual da página (nunca no
nome do arquivo, que você não recebe):

- RG (carteira de identidade, frente ou verso)
- CNH (Carteira Nacional de Habilitação — traz RG, CPF e filiação num único documento)
- CPF (comprovante de inscrição no CPF)
- MAT-IMV (matrícula de imóvel, documento de cartório de registro de imóveis)
- CAR (recibo do Cadastro Ambiental Rural — SICAR)
- CADPRO (comprovante de Cadastro de Produtor Rural)
- COMP-RES (comprovante de residência — conta de luz, água, telefone, etc.)
- NAO_IDENTIFICADO (não se encaixa em nenhum tipo acima, ou está ilegível)

Responda APENAS com um JSON válido, sem texto antes ou depois:

{"tipo_documento": "RG|CNH|CPF|MAT-IMV|CAR|CADPRO|COMP-RES|NAO_IDENTIFICADO", "confianca": 0-100, "motivo": "string curta"}
"""

PROMPTS_EXTRACAO = {
    "RG": """Extraia os dados desta carteira de identidade (RG). Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_rg": "string ou null", "orgao_emissor": "string ou null", "data_nascimento": "AAAA-MM-DD ou null", "filiacao_mae": "string ou null", "filiacao_pai": "string ou null", "confianca": 0-100}
Se um campo não estiver legível, retorne null — nunca invente um valor.""",
    "CNH": """Extraia os dados desta CNH. Ela costuma trazer RG, CPF e filiação juntos -- extraia todos os campos presentes. Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_registro_cnh": "string ou null", "numero_rg": "string ou null", "orgao_emissor": "string ou null", "numero_cpf": "string ou null", "data_nascimento": "AAAA-MM-DD ou null", "filiacao_mae": "string ou null", "filiacao_pai": "string ou null", "confianca": 0-100}
Se um campo não estiver legível, retorne null — nunca invente um valor.""",
    "CPF": """Extraia os dados deste comprovante de inscrição no CPF. Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_cpf": "string ou null", "confianca": 0-100}""",
    "MAT-IMV": """Extraia os dados desta matrícula de imóvel. Responda apenas com JSON:
{"numero_matricula": "string ou null", "cartorio": "string ou null", "comarca": "string ou null", "area_ha": "number ou null", "proprietario": "string ou null", "confianca": 0-100}""",
    "CAR": """Extraia os dados deste recibo do CAR (SICAR). Responda apenas com JSON:
{"numero_car": "string ou null", "municipio": "string ou null", "uf": "string ou null", "area_total_ha": "number ou null", "titular": "string ou null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}""",
    "CADPRO": """Extraia os dados deste comprovante de Cadastro de Produtor Rural. Responda apenas com JSON:
{"numero_protocolo": "string ou null", "titular": "string ou null", "atividade_declarada": "string ou null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}""",
    "COMP-RES": """Extraia os dados deste comprovante de residência. Responda apenas com JSON:
{"nome_titular": "string ou null", "logradouro": "string ou null", "numero": "string ou null", "bairro": "string ou null", "municipio": "string ou null", "uf": "string ou null", "cep": "string ou null", "tipo_comprovante": "luz|agua|telefone|outro|null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}""",
}

client = anthropic.Anthropic()


def pdf_tem_camada_de_texto(pdf_path: Path) -> bool:
    """PDF nativo (CADPRO emitido digitalmente etc.) tem texto extraível;
    PDF escaneado não. Evita gastar chamada de visão quando não precisa."""
    result = subprocess.run(
        ["pdftotext", str(pdf_path), "-"], capture_output=True, text=True
    )
    return len(result.stdout.strip()) > 20


def pdf_para_imagens(pdf_path: Path, saida_dir: Path) -> list[Path]:
    """Converte cada página do PDF em uma imagem PNG. Usa pdftoppm, o mesmo
    utilitário já usado no pipeline de verificação visual do docxtpl."""
    saida_dir.mkdir(parents=True, exist_ok=True)
    prefixo = saida_dir / pdf_path.stem
    subprocess.run(
        ["pdftoppm", "-png", "-r", "200", str(pdf_path), str(prefixo)], check=True
    )
    return sorted(saida_dir.glob(f"{pdf_path.stem}-*.png"))


def preparar_imagem(imagem_path: Path) -> bytes:
    """Redimensiona e recomprime a imagem antes de enviar -- fotos de
    celular sincronizadas sem compressão estouram o limite de tamanho da
    API. Sempre devolve JPEG, mesmo que a origem seja PNG."""
    with Image.open(imagem_path) as img:
        img = img.convert("RGB")
        if max(img.size) > LADO_MAXIMO_IMAGEM:
            escala = LADO_MAXIMO_IMAGEM / max(img.size)
            novo_tamanho = (round(img.width * escala), round(img.height * escala))
            img = img.resize(novo_tamanho, Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
        return buffer.getvalue()


def chamar_claude_json(prompt: str, imagem_path: Path) -> dict:
    imagem_bytes = preparar_imagem(imagem_path)
    imagem_b64 = base64.standard_b64encode(imagem_bytes).decode()
    media_type = "image/jpeg"
    resposta = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": imagem_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    texto = resposta.content[0].text.strip()
    texto = re.sub(r"^```json\s*|\s*```$", "", texto.strip())
    return json.loads(texto)


def gerar_nome_final(cpf_ou_cnpj: str, tipo_doc: str, extensao: str) -> str:
    doc_limpo = re.sub(r"\D", "", cpf_ou_cnpj) if cpf_ou_cnpj else "SEM-DOC"
    hoje = date.today().isoformat()
    return f"{doc_limpo}_{tipo_doc}_{hoje}{extensao}"


def processar_pagina(imagem_path: Path, origem: str) -> dict:
    classificacao = chamar_claude_json(PROMPT_CLASSIFICACAO, imagem_path)
    tipo = classificacao.get("tipo_documento", "NAO_IDENTIFICADO")
    confianca_class = classificacao.get("confianca", 0)

    resultado = {
        "arquivo_origem": origem,
        "tipo_documento": tipo,
        "confianca_classificacao": confianca_class,
        "campos_extraidos": None,
        "confianca_extracao": None,
        "status": None,
    }

    if tipo == "NAO_IDENTIFICADO" or confianca_class < CONFIANCA_MINIMA:
        resultado["status"] = "revisao_manual"
        resultado["motivo"] = classificacao.get("motivo", "confiança de classificação baixa")
        return resultado

    prompt_extracao = PROMPTS_EXTRACAO[tipo]
    extracao = chamar_claude_json(prompt_extracao, imagem_path)
    confianca_extr = extracao.get("confianca", 0)
    resultado["campos_extraidos"] = extracao
    resultado["confianca_extracao"] = confianca_extr

    resultado["status"] = "auto" if confianca_extr >= CONFIANCA_MINIMA else "revisao_manual"
    return resultado


def processar_arquivo(caminho: Path) -> list[dict]:
    resultados = []

    if caminho.suffix.lower() == ".pdf":
        if pdf_tem_camada_de_texto(caminho):
            # TODO: caminho de extração por texto (pdfplumber) para PDFs
            # nativos como o CADPRO digital — não implementado aqui porque
            # depende do layout específico de cada emissor.
            resultados.append(
                {"arquivo_origem": str(caminho), "status": "extracao_texto_pendente"}
            )
            return resultados
        paginas = pdf_para_imagens(caminho, PROCESSADOS_DIR / "_tmp_paginas")
    else:
        paginas = [caminho]

    for pagina in paginas:
        resultados.append(processar_pagina(pagina, origem=str(caminho)))

    return resultados


def rotear_resultado(resultado: dict, arquivo_original: Path):
    if resultado["status"] == "auto":
        campos = resultado["campos_extraidos"]
        doc_chave = (
            campos.get("numero_cpf")
            or campos.get("numero_rg")
            or campos.get("nome_completo", "")
        )
        nome_final = gerar_nome_final(
            doc_chave, resultado["tipo_documento"], arquivo_original.suffix
        )
        destino = PROCESSADOS_DIR / nome_final
        PROCESSADOS_DIR.mkdir(exist_ok=True)
        arquivo_original.replace(destino)
        (destino.with_suffix(".json")).write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2)
        )
    else:
        REVISAO_DIR.mkdir(exist_ok=True)
        destino = REVISAO_DIR / arquivo_original.name
        arquivo_original.replace(destino)
        (destino.with_suffix(".json")).write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2)
        )
        notificar_telegram_revisao(destino, resultado)


def notificar_telegram_revisao(arquivo: Path, resultado: dict):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    texto = (
        f"Documento para revisão manual: {arquivo.name}\n"
        f"Tipo detectado: {resultado.get('tipo_documento', '?')}\n"
        f"Motivo: {resultado.get('motivo', 'confiança abaixo do limite')}"
    )
    import urllib.request

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    dados = json.dumps({"chat_id": chat_id, "text": texto}).encode()
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req)


def main():
    INBOX_DIR.mkdir(exist_ok=True)
    for arquivo in sorted(INBOX_DIR.iterdir()):
        if arquivo.is_file():
            resultados = processar_arquivo(arquivo)
            for resultado in resultados:
                # PDF multi-página: cada resultado veio de uma imagem
                # temporária, não do arquivo original — roteamento por
                # página fica como próximo incremento se o volume de
                # documentos combinados num único scan justificar.
                if "campos_extraidos" in resultado or resultado["status"] == "revisao_manual":
                    origem = Path(resultado["arquivo_origem"])
                    if origem == arquivo:
                        rotear_resultado(resultado, arquivo)


if __name__ == "__main__":
    main()
