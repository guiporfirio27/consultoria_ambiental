# -*- coding: utf-8 -*-
"""
odoo_mapa.py — liga o gerador do Mapa de Situação ao Odoo.

Lê os dados pelo Processo (project.project), segue a cadeia
Processo → Empreendimento → Imóvel / Empreendedor, confere se dá para gerar,
roda o gerar_mapa_situacao.py e devolve o resultado ao Odoo:

  * PDF anexado ao Processo, com mensagem no histórico (chatter);
  * área livre gravada no Empreendimento (decisão D2);
  * município da malha do IAT gravado no Imóvel (decisão D4);
  * status, impressão digital (hash) e data do mapa no Processo.

Este módulo NÃO depende do QGIS para ler, conferir e gravar no Odoo — só a
função processar_processo() importa o gerador. Por isso dá para testar a
parte do Odoo fora do QGIS.

Credenciais (nesta ordem):
  1. variáveis de ambiente ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY
     (os mesmos nomes do pipeline de documentos);
  2. arquivo  %USERPROFILE%\\.gp_odoo.json  (Windows) ou ~/.gp_odoo.json:
       {"url": "https://gp-construcaoengenharia.odoo.com",
        "db": "<nome do banco; se omitido, usa o subdomínio da url>",
        "email": "guiporfirio27@gmail.com",
        "api_key": "<chave API_mapa_qgis>"}
  Nunca coloque a chave dentro deste arquivo nem na pasta Dados GIS.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import xmlrpc.client
from datetime import date, datetime, timezone
from pathlib import Path

COMPANY_ID = 2  # GP Consultoria Ambiental

PADRAO_CAR = re.compile(r"^[A-Z]{2}-\d{7}-[0-9A-F]{32}$")

# Campos que definem o conteúdo do mapa. Mudou algum, o mapa fica
# desatualizado (seção 3.3 do plano). O município NÃO entra: ele vem da
# malha e é corrigido pelo próprio gerador.
CAMPOS_DO_HASH = (
    "cliente", "doc_cliente", "cod_imovel", "estrutura_coord",
    "endereco_modelo", "area_construida_m2", "app_faixa",
)


class ErroOdoo(Exception):
    pass


# --------------------------------------------------------------------------
# Conexão
# --------------------------------------------------------------------------

def carregar_credenciais():
    env = {k: os.environ.get(k) for k in ("ODOO_URL", "ODOO_DB", "ODOO_EMAIL", "ODOO_API_KEY")}
    if all(env.values()):
        return {"url": env["ODOO_URL"], "db": env["ODOO_DB"],
                "email": env["ODOO_EMAIL"], "api_key": env["ODOO_API_KEY"]}

    arquivo = Path.home() / ".gp_odoo.json"
    if not arquivo.exists():
        raise ErroOdoo(
            f"Credenciais do Odoo não encontradas. Crie o arquivo {arquivo} com "
            '{"url": ..., "db": ..., "email": ..., "api_key": ...} '
            "ou defina ODOO_URL, ODOO_DB, ODOO_EMAIL e ODOO_API_KEY.")
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    # db vazio ou ainda com o texto do modelo -> subdomínio da url
    if dados.get("url") and (not dados.get("db") or "COLOQUE" in str(dados["db"]).upper()):
        dados["db"] = banco_padrao(dados["url"])
    if "COLE_A_CHAVE" in str(dados.get("api_key", "")).upper():
        dados["api_key"] = ""
    faltando = [k for k in ("url", "db", "email", "api_key") if not dados.get(k)]
    if faltando:
        raise ErroOdoo(f"{arquivo} sem: {', '.join(faltando)}")
    return dados


def banco_padrao(url):
    """No Odoo online (odoo.com), o nome do banco costuma ser o subdomínio:
    https://gp-construcaoengenharia.odoo.com -> gp-construcaoengenharia."""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return host.split(".")[0]


class Odoo:
    def __init__(self, url, db, email, api_key):
        self.url = url.rstrip("/").removesuffix("/odoo")
        self.db, self.api_key = db, api_key
        comum = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        try:
            self.uid = comum.authenticate(db, email, api_key, {})
        except xmlrpc.client.Fault as e:
            if "does not exist" in str(e.faultString):
                raise ErroOdoo(
                    f"O banco '{db}' não existe neste Odoo. Confira o nome em "
                    "odoo.com > Meus bancos de dados e rode o configurar_credenciais.py.") from None
            raise
        if not self.uid:
            raise ErroOdoo("Login no Odoo recusado — confira e-mail, banco e chave de API.")
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def call(self, modelo, metodo, *args, **kwargs):
        kwargs.setdefault("context", {})
        kwargs["context"].setdefault("allowed_company_ids", [COMPANY_ID])
        return self._models.execute_kw(self.db, self.uid, self.api_key,
                                       modelo, metodo, list(args), kwargs)

    def ler(self, modelo, rid, campos):
        r = self.call(modelo, "read", [rid], campos)
        if not r:
            raise ErroOdoo(f"{modelo} {rid} não encontrado (ou sem acesso).")
        return r[0]

    def tem_campos(self, modelo, campos):
        existentes = self.call(modelo, "fields_get", campos, attributes=["type"])
        return set(existentes)


def conectar():
    c = carregar_credenciais()
    return Odoo(c["url"], c["db"], c["email"], c["api_key"])


# --------------------------------------------------------------------------
# Validações
# --------------------------------------------------------------------------

def so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def cpf_valido(cpf):
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(d[n]):
            return False
    return True


def cnpj_valido(cnpj):
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(int(d[i]) * pesos[i] for i in range(pos))
        dv = 0 if soma % 11 < 2 else 11 - soma % 11
        if dv != int(d[pos]):
            return False
    return True


def formatar_doc(doc):
    d = so_digitos(doc)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return doc


def normalizar_car(car):
    return re.sub(r"\s", "", (car or "")).upper()


def normalizar_cep(cep):
    d = so_digitos(cep)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else None


def _nome(m2o):
    return m2o[1] if m2o else None


def _id(m2o):
    return m2o[0] if m2o else None


def montar_endereco(imovel):
    """Endereço do EMPREENDIMENTO (não do requerente). {municipio} é trocado
    pelo gerador pelo município da malha do IAT (D4)."""
    partes = [imovel.get("x_denominacao"), imovel.get("x_logradouro"),
              imovel.get("x_localidade"), "Zona Rural", "{municipio}/PR"]
    endereco = " - ".join(p.strip() for p in partes if p and str(p).strip())
    cep = normalizar_cep(imovel.get("x_cep"))
    if cep:
        endereco += f" - CEP {cep}"
    return endereco


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------

def ler_dados_processo(odoo, processo_id):
    """Devolve (dados, pendencias, avisos). Pendência bloqueia o mapa; aviso
    não bloqueia mas vai para o histórico."""
    pendencias, avisos = [], []

    proc = odoo.ler("project.project", processo_id,
                    ["name", "company_id", "x_empreendimento_id"])
    if _id(proc["company_id"]) != COMPANY_ID:
        raise ErroOdoo(f"Processo {processo_id} não é da GP Consultoria Ambiental "
                       f"(empresa: {_nome(proc['company_id'])}).")

    dados = {"processo_id": processo_id, "processo_nome": proc["name"]}

    if not proc.get("x_empreendimento_id"):
        pendencias.append("Processo sem Empreendimento ligado.")
        return dados, pendencias, avisos

    emp = odoo.ler("x_empreendimento", _id(proc["x_empreendimento_id"]),
                   ["x_name", "x_imovel_id", "x_empreendedor_id", "x_coordenada",
                    "x_area_construida_m2", "x_app_faixa_m"])
    dados["empreendimento_id"] = emp["id"]

    # Coordenada da estrutura principal (anamnese)
    coord = (emp.get("x_coordenada") or "").strip()
    if not coord:
        pendencias.append("Empreendimento sem coordenada da estrutura principal (anamnese).")
    dados["estrutura_coord"] = coord

    # Área construída (anamnese, m² → ha)
    m2 = emp.get("x_area_construida_m2") or 0.0
    dados["area_construida_m2"] = m2
    if m2 <= 0:
        avisos.append("Área construída não informada: área construída e área livre "
                      "ficarão fora do cartucho (a IN exige).")

    dados["app_faixa"] = int(emp.get("x_app_faixa_m") or 30)

    # Empreendedor
    if not emp.get("x_empreendedor_id"):
        pendencias.append("Empreendimento sem Empreendedor.")
    else:
        pessoa = odoo.ler("res.partner", _id(emp["x_empreendedor_id"]), ["name", "vat"])
        dados["cliente"] = (pessoa.get("name") or "").strip()
        vat = pessoa.get("vat") or ""
        if not vat:
            pendencias.append(f"Empreendedor '{dados['cliente']}' sem CPF/CNPJ.")
        elif not (cpf_valido(vat) or cnpj_valido(vat)):
            pendencias.append(f"CPF/CNPJ do empreendedor inválido: {vat}.")
        dados["doc_cliente"] = formatar_doc(vat)

    # Imóvel
    if not emp.get("x_imovel_id"):
        pendencias.append("Empreendimento sem Imóvel principal.")
    else:
        imovel = odoo.ler("x_imovel", _id(emp["x_imovel_id"]),
                          ["x_name", "x_numero_car", "x_denominacao", "x_logradouro",
                           "x_localidade", "x_municipio", "x_cep", "x_car_area_total_ha"])
        dados["imovel_id"] = imovel["id"]
        car = normalizar_car(imovel.get("x_numero_car"))
        if not car:
            pendencias.append("Imóvel sem número do CAR (recibo do CAR).")
        elif not PADRAO_CAR.match(car):
            pendencias.append(f"Número do CAR fora do padrão PR-0000000-<32 caracteres>: {car}.")
        dados["cod_imovel"] = car
        dados["municipio_cadastro"] = imovel.get("x_municipio") or None
        dados["car_area_recibo_ha"] = imovel.get("x_car_area_total_ha") or None
        if imovel.get("x_cep") and not normalizar_cep(imovel["x_cep"]):
            avisos.append(f"CEP inválido no Imóvel ({imovel['x_cep']}): omitido do endereço.")
        dados["endereco_modelo"] = montar_endereco(imovel)

    return dados, pendencias, avisos


def hash_dados(dados):
    base = {k: dados.get(k) for k in CAMPOS_DO_HASH}
    texto = json.dumps(base, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def nome_arquivo_pdf(dados):
    car = dados.get("cod_imovel") or "SEM-CAR"
    curto = car.split("-")[-1][:8] if "-" in car else car[:8]
    return f"MAPA-SIT_{curto}_{date.today().isoformat()}.pdf"


# --------------------------------------------------------------------------
# Argumentos do gerador
# --------------------------------------------------------------------------

def montar_argv(dados, pasta_gis, saida, margem_m=400, osm=True, qpt=None):
    def p(nome):
        return str(Path(pasta_gis) / nome)

    argv = [
        "--dados-dir", str(pasta_gis),
        "--cod-imovel", dados["cod_imovel"],
        "--estrutura-coord", dados["estrutura_coord"],
        "--cliente", dados["cliente"],
        "--doc-cliente", dados["doc_cliente"],
        "--endereco", dados["endereco_modelo"],
        "--app-faixa", str(dados["app_faixa"]),
        "--margem-m", str(margem_m),
        "--logo", p("logo_gp_consultoria.png"),
        "--logo-fonte", p("logo_iat.png"),
        "--rosa-ventos", p("rosa_dos_ventos.svg"),
        "--saida", str(saida),
    ]
    if dados.get("area_construida_m2"):
        argv += ["--area-construida-ha", f"{dados['area_construida_m2'] / 10000:.4f}"]
    if osm:
        argv += ["--osm-referencias"]
    if qpt:
        argv += ["--salvar-qpt", p(qpt)]
    return argv


# --------------------------------------------------------------------------
# Gravação no Odoo
# --------------------------------------------------------------------------

def _escrever_status(odoo, processo_id, valores):
    existentes = odoo.tem_campos("project.project", list(valores))
    filtrados = {k: v for k, v in valores.items() if k in existentes}
    if filtrados:
        odoo.call("project.project", "write", [processo_id], filtrados)


def _postar(odoo, processo_id, texto, anexos=None):
    kwargs = {"body": texto, "message_type": "comment", "subtype_xmlid": "mail.mt_note"}
    if anexos:
        kwargs["attachment_ids"] = anexos
    odoo.call("project.project", "message_post", [processo_id], **kwargs)


def registrar_pendencias(odoo, processo_id, pendencias):
    _escrever_status(odoo, processo_id, {
        "x_mapa_status": "aguardando",
        "x_mapa_pendencias": "\n".join(f"- {p}" for p in pendencias),
    })


def registrar_gerando(odoo, processo_id):
    _escrever_status(odoo, processo_id, {"x_mapa_status": "gerando"})


def registrar_erro(odoo, processo_id, motivo):
    _escrever_status(odoo, processo_id, {
        "x_mapa_status": "erro",
        "x_mapa_pendencias": motivo[:2000],
    })
    _postar(odoo, processo_id, f"Mapa de situação NÃO gerado. Motivo: {motivo[:1500]}")


def registrar_sucesso(odoo, dados, resultado, pdf, avisos):
    processo_id = dados["processo_id"]
    notas = []

    # 1. PDF anexado ao Processo
    conteudo = Path(pdf).read_bytes()
    anexo_id = odoo.call("ir.attachment", "create", {
        "name": Path(pdf).name,
        "datas": base64.b64encode(conteudo).decode(),
        "res_model": "project.project",
        "res_id": processo_id,
        "mimetype": "application/pdf",
    })

    # 2. Área livre no Empreendimento (D2)
    if resultado.get("area_livre_ha") is not None and dados.get("empreendimento_id"):
        odoo.call("x_empreendimento", "write", [dados["empreendimento_id"]],
                  {"x_area_livre_ha": resultado["area_livre_ha"]})

    # 3. Município da malha no Imóvel (D4)
    mun = resultado.get("municipio")
    if mun and dados.get("imovel_id"):
        antigo = dados.get("municipio_cadastro")
        if (antigo or "").strip().lower() != mun.strip().lower():
            odoo.call("x_imovel", "write", [dados["imovel_id"]], {"x_municipio": mun})
            if antigo:
                notas.append(f"Município corrigido pela malha do IAT: {antigo} → {mun}.")

    # 4. Conferência da área do recibo x polígono
    recibo = dados.get("car_area_recibo_ha")
    total = resultado.get("area_total_ha")
    if recibo and total and abs(total - recibo) / recibo > 0.02:
        notas.append(f"Área do polígono do CAR ({total:.4f} ha) difere mais de 2% "
                     f"da área do recibo ({recibo:.4f} ha).")

    # 5. Status e histórico
    _escrever_status(odoo, processo_id, {
        "x_mapa_status": "gerado",
        "x_mapa_hash": hash_dados(dados),
        "x_mapa_pendencias": False,
        "x_mapa_gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    })

    linhas = [f"Mapa de situação gerado ({Path(pdf).name})."]
    if total is not None:
        linhas.append(f"Área total (CAR): {total:.4f} ha.")
    if resultado.get("area_livre_ha") is not None:
        linhas.append(f"Área livre: {resultado['area_livre_ha']:.4f} ha.")
    if mun:
        linhas.append(f"Município (malha IAT): {mun}.")
    linhas += notas + avisos
    linhas.append(f"Gerador {resultado.get('versao', '?')}.")
    _postar(odoo, processo_id, " ".join(linhas), anexos=[anexo_id])
    return anexo_id


# --------------------------------------------------------------------------
# Execução completa (precisa do QGIS)
# --------------------------------------------------------------------------

class _Tee(io.TextIOBase):
    """Escreve no console e guarda uma cópia, para levar os avisos do log
    do gerador ao histórico do Processo."""
    def __init__(self, original):
        self.original, self.buffer = original, io.StringIO()

    def write(self, texto):
        self.buffer.write(texto)
        return self.original.write(texto)

    def flush(self):
        self.original.flush()


def _avisos_do_log(log):
    return [l.strip() for l in log.splitlines()
            if l.strip().startswith(("[aviso]", "[AVISO]"))][:8]


def processar_processo(processo_id, pasta_gis, margem_m=400, osm=True, qpt=None,
                       gravar_no_odoo=True, somente_conferir=False, odoo=None):
    """Gera o mapa de um Processo. Devolve o caminho do PDF, ou None."""
    odoo = odoo or conectar()
    dados, pendencias, avisos = ler_dados_processo(odoo, processo_id)

    print(f"[info] Processo {processo_id}: {dados.get('processo_nome')}")
    for k in ("cliente", "doc_cliente", "cod_imovel", "estrutura_coord",
              "endereco_modelo", "area_construida_m2", "app_faixa"):
        print(f"       {k}: {dados.get(k)}")
    for a in avisos:
        print(f"[aviso] {a}")

    if pendencias:
        print("[BLOQUEADO] O mapa não pode ser gerado:")
        for p in pendencias:
            print(f"   - {p}")
        if gravar_no_odoo and not somente_conferir:
            registrar_pendencias(odoo, processo_id, pendencias)
        return None

    if somente_conferir:
        print("[ok] Dados completos. (Modo só conferência: nada foi gerado nem gravado.)")
        return None

    saida = Path(pasta_gis) / "saidas" / nome_arquivo_pdf(dados)
    argv = montar_argv(dados, pasta_gis, saida, margem_m=margem_m, osm=osm, qpt=qpt)

    if gravar_no_odoo:
        registrar_gerando(odoo, processo_id)

    if str(pasta_gis) not in sys.path:
        sys.path.insert(0, str(pasta_gis))
    import importlib
    import gerar_mapa_situacao
    importlib.reload(gerar_mapa_situacao)

    tee_out, tee_err = _Tee(sys.stdout), _Tee(sys.stderr)
    try:
        with contextlib.redirect_stdout(tee_out), contextlib.redirect_stderr(tee_err):
            resultado = gerar_mapa_situacao.main(argv)
    except SystemExit as e:
        motivo = (tee_err.buffer.getvalue().strip().splitlines() or [f"código {e.code}"])[-1]
        if gravar_no_odoo:
            registrar_erro(odoo, processo_id, f"Gerador interrompido: {motivo}")
        print(f"[ERRO] {motivo}")
        return None
    except Exception as e:
        if gravar_no_odoo:
            registrar_erro(odoo, processo_id, f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}")
        return None

    avisos += _avisos_do_log(tee_out.buffer.getvalue())
    if gravar_no_odoo:
        registrar_sucesso(odoo, dados, resultado or {}, saida, avisos)
        print(f"[ok] PDF anexado ao Processo {processo_id} e status 'Gerado' gravado.")
    return saida
