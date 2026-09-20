"""
Pipeline de classificação e extração de documentos — GP Consultoria Ambiental.

Fluxo: arquivo (PDF ou imagem) -> páginas -> classificação por página ->
consolidação por tipo de documento -> extração -> decisão (grava
automaticamente / vai para revisão humana).

MUDANÇAS DESTA VERSÃO (correção dos defeitos D2, D3 e D5 da auditoria):

  D2 — PDF multipágina derrubava a execução inteira. A versão anterior
       chamava rotear_resultado() uma vez por página, sempre sobre o MESMO
       arquivo físico: a primeira chamada movia o arquivo, a segunda
       estourava FileNotFoundError e matava o processo, deixando toda a fila
       seguinte sem processar. Agora o arquivo físico é movido UMA vez, para
       ./arquivos/, e as páginas são consolidadas por tipo de documento antes
       de qualquer decisão. Matrícula de imóvel e recibo do CAR — quase
       sempre multipágina — passam a funcionar.

  D3 — PDF com camada de texto virava buraco negro. A versão anterior
       devolvia status "extracao_texto_pendente" para PDFs nativos (CPF da
       Receita, recibo do CAR do SICAR, CADPRO digital): não gerava JSON, não
       notificava, não saía da pasta do Drive, e era rebaixado a cada
       execução, para sempre. Agora TODO PDF é rasterizado e lido por visão.
       Simples, uniforme, e elimina a categoria inteira de falha silenciosa.

  D5 — Nome final colidia. {cpf}_{tipo}_{data} fazia frente e verso do RG se
       sobrescreverem. Resolvido em dois níveis: o nome agora inclui um hash
       curto do conteúdo, E frente/verso do mesmo documento agora são
       consolidados num único registro em vez de dois arquivos concorrentes.

Consolidação por tipo: um scan pode conter páginas de documentos diferentes
(RG + CPF na mesma digitalização). Cada página é classificada; páginas do
mesmo tipo têm seus campos fundidos (o primeiro valor não-nulo vence, o que
é exatamente o comportamento certo para frente/verso); cada tipo distinto
gera um JSON próprio, todos apontando para o mesmo arquivo físico.

Prompts de referência: prompts-classificacao-extracao-documentos.md.

Variáveis de ambiente:
  ANTHROPIC_API_KEY   -- chamada à API do Claude
  TELEGRAM_TOKEN       -- notificação da fila de revisão (opcional)
  TELEGRAM_CHAT_ID     -- para onde a notificação vai (opcional)
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

import anthropic
from PIL import Image

MODEL = "claude-sonnet-5"
CONFIANCA_MINIMA = 70
MAX_PAGINAS = 15  # trava de custo: acima disso, revisão humana decide
LADO_MAXIMO_IMAGEM = 1568  # recomendação da Anthropic para imagens de visão
QUALIDADE_JPEG = 85
DPI_RASTERIZACAO = 200

INBOX_DIR = Path("./inbox")
ARQUIVOS_DIR = Path("./arquivos")        # onde o binário original passa a viver
PROCESSADOS_DIR = Path("./processados")  # JSONs prontos para gravar no Odoo
REVISAO_DIR = Path("./revisao_manual")   # JSONs que precisam de olho humano
TMP_DIR = Path("./_tmp_paginas")         # imagens de página, descartáveis

# Arquivo que o coletor deixa ao lado do documento dizendo de onde ele veio e,
# quando já se sabe, de qual contato. Ver ler_contexto().
SUFIXO_CONTEXTO = ".contexto.json"

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

Documentos longos (matrícula, CAR) têm páginas de continuação sem cabeçalho:
classifique-as pelo conteúdo, não exija o cabeçalho para reconhecer o tipo.

Responda APENAS com um JSON válido, sem texto antes ou depois:

{"tipo_documento": "RG|CNH|CPF|MAT-IMV|CAR|CADPRO|COMP-RES|NAO_IDENTIFICADO", "confianca": 0-100, "motivo": "string curta"}
"""

PROMPTS_EXTRACAO = {
    "RG": """Extraia os dados desta carteira de identidade (RG). Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_rg": "string ou null", "orgao_emissor": "string ou null", "numero_cpf": "string ou null", "data_nascimento": "AAAA-MM-DD ou null", "filiacao_mae": "string ou null", "filiacao_pai": "string ou null", "confianca": 0-100}
Esta pode ser a frente OU o verso do documento — extraia apenas o que estiver
visível nesta página e retorne null para o resto. Nunca invente um valor.""",
    "CNH": """Extraia os dados desta CNH. Ela costuma trazer RG, CPF e filiação juntos -- extraia todos os campos presentes. Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_registro_cnh": "string ou null", "numero_rg": "string ou null", "orgao_emissor": "string ou null", "numero_cpf": "string ou null", "data_nascimento": "AAAA-MM-DD ou null", "filiacao_mae": "string ou null", "filiacao_pai": "string ou null", "confianca": 0-100}
Se um campo não estiver legível, retorne null — nunca invente um valor.""",
    "CPF": """Extraia os dados deste comprovante de inscrição no CPF. Responda apenas com JSON:
{"nome_completo": "string ou null", "numero_cpf": "string ou null", "confianca": 0-100}""",
    "MAT-IMV": """Extraia os dados desta matrícula de imóvel. Responda apenas com JSON:
{"numero_matricula": "string ou null", "cartorio": "string ou null", "comarca": "string ou null", "municipio": "string ou null", "area_ha": "number ou null", "titular": "string ou null", "documento_titular": "string ou null", "confianca": 0-100}

Sobre "titular" e "documento_titular": traga o nome do proprietário atual
registrado e o CPF ou CNPJ dele, exatamente como aparecem no documento. Se a
matrícula tiver histórico de transmissões, use o proprietário ATUAL (o do
último registro), não os anteriores. Se o CPF/CNPJ não aparecer, retorne null
em documento_titular — nunca deduza a partir do nome.

Esta pode ser uma página de continuação: extraia só o que estiver visível
nesta página e retorne null para o resto.""",
    "CAR": """Extraia os dados deste recibo do CAR (SICAR). Responda apenas com JSON:
{"numero_car": "string ou null", "municipio": "string ou null", "uf": "string ou null", "area_total_ha": "number ou null", "titular": "string ou null", "documento_titular": "string ou null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}

Sobre "titular" e "documento_titular": traga o nome do titular do cadastro e o
CPF ou CNPJ dele, exatamente como aparecem no recibo. ATENÇÃO: o titular de um
CAR NÃO é necessariamente o proprietário do imóvel — pode ser arrendatário,
posseiro ou comodatário. Extraia apenas o que o documento declara, sem
interpretar o papel dessa pessoa. Se o CPF/CNPJ não aparecer, retorne null.

Esta pode ser uma página de continuação: extraia só o que estiver visível
nesta página e retorne null para o resto.""",
    "CADPRO": """Extraia os dados deste comprovante de Cadastro de Produtor Rural. Responda apenas com JSON:
{"numero_protocolo": "string ou null", "titular": "string ou null", "documento_titular": "string ou null", "municipio": "string ou null", "atividade_declarada": "string ou null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}

Sobre "documento_titular": o CPF ou CNPJ do titular, como aparece no
documento. Se não aparecer, retorne null — nunca deduza a partir do nome.""",
    "COMP-RES": """Extraia os dados deste comprovante de residência. Responda apenas com JSON:
{"nome_titular": "string ou null", "logradouro": "string ou null", "numero": "string ou null", "bairro": "string ou null", "municipio": "string ou null", "uf": "string ou null", "cep": "string ou null", "tipo_comprovante": "luz|agua|telefone|outro|null", "data_emissao": "AAAA-MM-DD ou null", "confianca": 0-100}""",
}

client = anthropic.Anthropic()


# ---------------------------------------------------------------- utilidades


def sha1_do_arquivo(caminho: Path) -> str:
    h = hashlib.sha1()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def rasterizar(caminho: Path) -> list[Path]:
    """Converte cada página do PDF em PNG. Diferente da versão anterior, NÃO
    há mais desvio para PDFs com camada de texto: rasterizar e ler por visão
    funciona igualmente bem para PDF nativo e para escaneado, e elimina o
    caminho morto que engolia CPF da Receita, recibo do SICAR e CADPRO
    digital sem deixar rastro."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    prefixo = TMP_DIR / caminho.stem
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(DPI_RASTERIZACAO), str(caminho), str(prefixo)],
        check=True,
    )
    return sorted(TMP_DIR.glob(f"{caminho.stem}-*.png"))


def paginas_do_arquivo(caminho: Path) -> list[Path]:
    if caminho.suffix.lower() == ".pdf":
        return rasterizar(caminho)
    return [caminho]


def preparar_imagem(imagem_path: Path) -> bytes:
    """Redimensiona e recomprime antes de enviar -- fotos de celular
    sincronizadas sem compressão estouram o limite de tamanho da API."""
    with Image.open(imagem_path) as img:
        img = img.convert("RGB")
        if max(img.size) > LADO_MAXIMO_IMAGEM:
            escala = LADO_MAXIMO_IMAGEM / max(img.size)
            img = img.resize(
                (round(img.width * escala), round(img.height * escala)), Image.LANCZOS
            )
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=QUALIDADE_JPEG, optimize=True)
        return buffer.getvalue()


def chamar_claude_json(prompt: str, imagem_path: Path) -> dict:
    imagem_b64 = base64.standard_b64encode(preparar_imagem(imagem_path)).decode()
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
                            "media_type": "image/jpeg",
                            "data": imagem_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    texto = re.sub(r"^```json\s*|\s*```$", "", resposta.content[0].text.strip())
    return json.loads(texto)


# ------------------------------------------------------------- consolidação


def fundir_extracoes(extracoes: list[dict]) -> dict:
    """Funde as extrações de várias páginas do MESMO tipo de documento.
    O primeiro valor não-nulo vence -- comportamento correto para frente e
    verso de RG (campos complementares) e para páginas de continuação de
    matrícula/CAR (o cabeçalho com os identificadores está na primeira)."""
    fundido: dict = {}
    for extracao in extracoes:
        for chave, valor in extracao.items():
            if chave == "confianca":
                continue
            if valor in (None, "", []) or fundido.get(chave) not in (None, ""):
                continue
            fundido[chave] = valor
    return fundido


def chave_identificadora(campos: dict) -> str:
    """Melhor identificador disponível para compor o nome do arquivo."""
    for chave in (
        "numero_cpf",
        "documento_titular",
        "numero_car",
        "numero_matricula",
        "numero_protocolo",
        "numero_rg",
    ):
        if campos.get(chave):
            return campos[chave]
    return campos.get("nome_completo") or campos.get("titular") or ""


def nome_final(identificador: str, checksum: str, extensao: str) -> str:
    """Inclui hash curto do conteúdo: dois arquivos diferentes do mesmo
    titular, mesmo tipo e mesmo dia não se sobrescrevem mais (D5)."""
    limpo = re.sub(r"\W", "", identificador or "")[:20] or "SEM-DOC"
    return f"{limpo}_{date.today().isoformat()}_{checksum[:8]}{extensao}"


# ------------------------------------------------------------ processamento


def ler_contexto(caminho: Path) -> dict:
    """Le o arquivo de contexto que o coletor deixou ao lado do documento.

    Este e o ponto central do desenho: um documento que chegou pelo WhatsApp
    ja nasce sabendo de qual contato veio, e um que veio da anamnese sabe de
    qual resposta. Só a digitalizacao e genuinamente anonima. Sem isso, o
    pipeline teria que adivinhar o dono por CPF em todos os casos -- que era
    de onde vinham os problemas dificeis (titular arrendatario, CPF sem
    correspondencia, imovel sem vinculo)."""
    arquivo = caminho.with_name(caminho.name + SUFIXO_CONTEXTO)
    if not arquivo.exists():
        return {"origem": "drive"}
    try:
        contexto = json.loads(arquivo.read_text(encoding="utf-8"))
    except (ValueError, OSError) as erro:
        print(f"  [aviso] contexto ilegivel para {caminho.name}: {erro}")
        return {"origem": "drive"}
    contexto.setdefault("origem", "outro")
    return contexto


def processar_arquivo(caminho: Path) -> list[dict]:
    """Devolve uma lista de resultados -- um por TIPO de documento encontrado
    no arquivo. O arquivo físico é único e compartilhado por todos eles."""
    checksum = sha1_do_arquivo(caminho)
    contexto = ler_contexto(caminho)
    base = {
        "arquivo_origem": str(caminho),  # marcar_drive_concluido.py depende desta chave
        "checksum_sha1": checksum,
        "origem": contexto.get("origem", "drive"),
        "partner_id": contexto.get("partner_id"),
        "imovel_id": contexto.get("imovel_id"),
        "processo_id": contexto.get("processo_id"),
    }

    try:
        paginas = paginas_do_arquivo(caminho)
    except subprocess.CalledProcessError as erro:
        return [
            {
                **base,
                "tipo_documento": "NAO_IDENTIFICADO",
                "campos_extraidos": None,
                "status": "revisao_manual",
                "motivo": f"falha ao abrir/rasterizar o arquivo: {erro}",
            }
        ]

    if not paginas:
        return [
            {
                **base,
                "tipo_documento": "NAO_IDENTIFICADO",
                "campos_extraidos": None,
                "status": "revisao_manual",
                "motivo": "nenhuma página legível no arquivo",
            }
        ]

    if len(paginas) > MAX_PAGINAS:
        return [
            {
                **base,
                "tipo_documento": "NAO_IDENTIFICADO",
                "campos_extraidos": None,
                "status": "revisao_manual",
                "motivo": (
                    f"{len(paginas)} páginas (limite {MAX_PAGINAS}) -- "
                    "confirme se é um documento só ou um lote a separar"
                ),
            }
        ]

    por_tipo: dict[str, list[dict]] = {}
    paginas_ignoradas: list[dict] = []

    for numero, pagina in enumerate(paginas, start=1):
        classificacao = chamar_claude_json(PROMPT_CLASSIFICACAO, pagina)
        tipo = classificacao.get("tipo_documento", "NAO_IDENTIFICADO")
        confianca = classificacao.get("confianca", 0)

        if tipo == "NAO_IDENTIFICADO" or tipo not in PROMPTS_EXTRACAO:
            paginas_ignoradas.append(
                {"pagina": numero, "motivo": classificacao.get("motivo", "não identificada")}
            )
            continue
        if confianca < CONFIANCA_MINIMA:
            paginas_ignoradas.append(
                {
                    "pagina": numero,
                    "motivo": f"confiança de classificação {confianca} < {CONFIANCA_MINIMA}",
                }
            )
            continue

        extracao = chamar_claude_json(PROMPTS_EXTRACAO[tipo], pagina)
        por_tipo.setdefault(tipo, []).append(
            {
                "pagina": numero,
                "confianca_classificacao": confianca,
                "extracao": extracao,
            }
        )

    if not por_tipo:
        return [
            {
                **base,
                "tipo_documento": "NAO_IDENTIFICADO",
                "campos_extraidos": None,
                "status": "revisao_manual",
                "motivo": "nenhuma página classificada com confiança suficiente",
                "paginas_ignoradas": paginas_ignoradas,
            }
        ]

    resultados = []
    for tipo, entradas in por_tipo.items():
        campos = fundir_extracoes([e["extracao"] for e in entradas])
        confianca_extr = max(e["extracao"].get("confianca", 0) for e in entradas)
        confianca_class = max(e["confianca_classificacao"] for e in entradas)
        resultados.append(
            {
                **base,
                "tipo_documento": tipo,
                "paginas": [e["pagina"] for e in entradas],
                "paginas_ignoradas": paginas_ignoradas,
                "confianca_classificacao": confianca_class,
                "confianca_extracao": confianca_extr,
                "campos_extraidos": campos,
                "status": "auto" if confianca_extr >= CONFIANCA_MINIMA else "revisao_manual",
                "motivo": (
                    ""
                    if confianca_extr >= CONFIANCA_MINIMA
                    else f"confiança de extração {confianca_extr} < {CONFIANCA_MINIMA}"
                ),
            }
        )
    return resultados


def arquivar_original(caminho: Path, resultados: list[dict]) -> Path:
    """Move o binário original para ./arquivos/ UMA única vez, com nome
    definitivo. Todos os JSONs daquele arquivo apontam para este caminho --
    é isso que elimina o FileNotFoundError da versão anterior (D2)."""
    ARQUIVOS_DIR.mkdir(exist_ok=True)
    melhor = max(
        resultados,
        key=lambda r: r.get("confianca_extracao") or 0,
    )
    identificador = chave_identificadora(melhor.get("campos_extraidos") or {})
    destino = ARQUIVOS_DIR / nome_final(
        identificador, melhor["checksum_sha1"], caminho.suffix
    )
    if destino.exists():
        destino.unlink()  # mesmo conteúdo, mesmo nome: reprocessamento do mesmo arquivo
    shutil.move(str(caminho), str(destino))

    # O contexto já foi lido e copiado para dentro de cada resultado; o arquivo
    # não pode ficar para trás na inbox ou seria reprocessado para sempre.
    contexto = caminho.with_name(caminho.name + SUFIXO_CONTEXTO)
    if contexto.exists():
        contexto.unlink()

    return destino


def gravar_resultados(resultados: list[dict], arquivo_arquivado: Path):
    PROCESSADOS_DIR.mkdir(exist_ok=True)
    REVISAO_DIR.mkdir(exist_ok=True)

    for resultado in resultados:
        resultado["arquivo_processado"] = str(arquivo_arquivado)
        pasta = PROCESSADOS_DIR if resultado["status"] == "auto" else REVISAO_DIR
        nome_json = f"{arquivo_arquivado.stem}__{resultado['tipo_documento']}.json"
        caminho_json = pasta / nome_json
        caminho_json.write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if resultado["status"] != "auto":
            notificar_telegram_revisao(caminho_json, resultado)


def notificar_telegram_revisao(arquivo: Path, resultado: dict):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    texto = (
        f"📄 Documento para revisão manual: {arquivo.name}\n"
        f"Origem: {resultado.get('arquivo_origem', '?')}\n"
        f"Tipo detectado: {resultado.get('tipo_documento', '?')}\n"
        f"Motivo: {resultado.get('motivo') or 'confiança abaixo do limite'}"
    )
    dados = json.dumps({"chat_id": chat_id, "text": texto}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=dados,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as erro:  # notificação nunca pode derrubar o pipeline
        print(f"[aviso] falha ao notificar Telegram: {erro}")


def limpar_paginas_temporarias():
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR, ignore_errors=True)


def main():
    INBOX_DIR.mkdir(exist_ok=True)
    for arquivo in sorted(INBOX_DIR.iterdir()):
        if not arquivo.is_file():
            continue
        # Os arquivos de contexto acompanham o documento, não são documentos.
        if arquivo.name.endswith(SUFIXO_CONTEXTO):
            continue
        print(f"Processando: {arquivo.name}")
        try:
            resultados = processar_arquivo(arquivo)
            arquivado = arquivar_original(arquivo, resultados)
            gravar_resultados(resultados, arquivado)
            tipos = ", ".join(r["tipo_documento"] for r in resultados)
            dono = resultados[0].get("partner_id")
            complemento = f" (contato {dono} já conhecido)" if dono else ""
            print(f"  -> {len(resultados)} resultado(s): {tipos}{complemento}")
        except Exception as erro:
            # Uma falha num arquivo não pode matar a fila inteira (era
            # exatamente o efeito do defeito D2). O arquivo fica no inbox e,
            # sem JSON, marcar_drive_concluido.py o mantém na fila do Drive
            # para nova tentativa na próxima execução.
            print(f"  [ERRO] {arquivo.name}: {erro}")
        finally:
            limpar_paginas_temporarias()


if __name__ == "__main__":
    main()
