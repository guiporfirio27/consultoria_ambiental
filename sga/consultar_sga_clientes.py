"""
Consulta SGA/IAT por cliente — licenças e processos anteriores do CPF/CNPJ.

Para cada Processo da GP Consultoria (empresa 2) ainda não consultado, ou com
"Consultar SGA de novo" marcado, busca no SGA do IAT os processos de
licenciamento do CPF/CNPJ do empreendedor e grava no próprio Processo:

  x_sga_licencas       lista legível (protocolo, atividade, modalidade,
                       documento, validade, situação, link)
  x_sga_situacao       resumo de uma linha
  x_sga_consultado_em  data/hora da consulta
  x_sga_consultar      volta para desmarcado

e publica um resumo no histórico do Processo. Bovinocultura vigente ou
vencida gera aviso (pode ser renovação ou regularização, não DLAM nova).

Mesmo endereço público usado pelo consulta_sga_parana_completo.py (repositório
monitor-editais-pr); aqui o filtro é o CPF/CNPJ em vez do município.

Roda no GitHub Actions (.github/workflows/consultar-sga.yml), de hora em hora.
O SGA só responde por http e não é acessível de todo lugar; se a consulta
falhar, nada é gravado e o Processo entra de novo na próxima rodada.

PRIVACIDADE: o log do GitHub Actions é público enquanto o repositório for
público. Por isso este script NÃO imprime nome, CPF/CNPJ nem dados das
licenças — só o número do Processo e contagens.

Variáveis de ambiente: ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY.
Opcional: SGA_LIMITE (máximo de Processos por rodada, padrão 30).
"""

import json
import os
import re
import sys
import time
import xmlrpc.client
from datetime import date, datetime, timezone

import requests

COMPANY_ID = 2
BASE_URL = "http://www.sga.pr.gov.br/sga-iap/consultarProcessoLicenciamento.do"
LINK_PUBLICO = ("http://www.sga.pr.gov.br/sga-iap/consultarProcessoLicenciamento.do"
                "?action=exibirDadosPublicoLicenca&numProtocolo={p}"
                "&indSia=false&ucsChamador=carregarInterfaceInicial")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": BASE_URL + "?action=carregarInterfaceInicial",
}
PAUSA_SEGUNDOS = 1.5


class ErroSGA(Exception):
    """SGA fora do ar ou resposta ilegível: não gravar nada, tentar depois."""


# --------------------------------------------------------------------------
# SGA
# --------------------------------------------------------------------------

def so_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def formatar_doc(d):
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d


def _corrigir_json(texto):
    # O SGA devolve um "JSON" com aspas simples e chaves sem aspas.
    texto = re.sub(r":\s*'([^']*)'", r': "\1"', texto)
    return re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', texto)


def _pagina(doc, start, limite, get=requests.get):
    params = {
        "action": "consultarProcessoLicenciamento",
        "_dc": str(int(time.time() * 1000)),
        "estadoMunicipioCombo": "PR",
        "municipioCombo": "",
        "numProtocolo": "", "numDocumento": "",
        "cpfCnpj": doc, "nomeRazaoSocial": "",
        "dtInicial": "", "dtFinal": "",
        "ckPesquisaAvancada": "on",
        "page": (start // limite) + 1, "start": start, "limit": limite,
    }
    try:
        resp = get(BASE_URL, params=params, headers=HEADERS, timeout=40)
    except requests.RequestException as e:
        raise ErroSGA(f"sem resposta do SGA ({type(e).__name__})")
    if resp.status_code != 200:
        raise ErroSGA(f"SGA respondeu HTTP {resp.status_code}")
    resp.encoding = "ISO-8859-1"
    texto = (resp.text or "").strip()
    for t in (texto, _corrigir_json(texto)):
        try:
            return json.loads(t)
        except ValueError:
            continue
    raise ErroSGA("resposta do SGA ilegível")


def consultar_documento(doc, get=requests.get):
    """Todos os processos do CPF/CNPJ (só dígitos). Tenta também o formato com
    pontuação, porque não está claro qual o SGA espera no filtro."""
    for valor in (doc, formatar_doc(doc)):
        itens, start, limite = [], 0, 100
        while True:
            data = _pagina(valor, start, limite, get=get)
            lista = data.get("listaProcessoLicenciamento") or []
            itens.extend(lista)
            start += limite
            if not lista or start >= int(data.get("total") or 0):
                break
            time.sleep(0.3)
        # O filtro precisa ter funcionado: todo item tem de ser deste documento.
        # (Se o SGA ignorasse o filtro, devolveria processos de outras pessoas.)
        itens = [i for i in itens if so_digitos(i.get("cpfCnpj")) == doc]
        if itens:
            return itens
    return []


def _dias(dt_str):
    try:
        return (datetime.strptime(dt_str, "%d/%m/%Y").date() - date.today()).days
    except (TypeError, ValueError):
        return None


def _situacao(dias):
    if dias is None:
        return "sem validade informada"
    if dias < 0:
        return "VENCIDA"
    if dias <= 180:
        return f"vigente, vence em {dias} dias"
    return "vigente"


def _eh_bovino(item):
    txt = ((item.get("descAtividade") or "") + " " + (item.get("descAtividadeEspecifica") or "")).lower()
    return "bovino" in txt


def montar_resultado(itens):
    """Devolve (texto da lista, situação de uma linha, aviso ou None)."""
    if not itens:
        return ("Nenhum processo de licenciamento encontrado no SGA para este CPF/CNPJ.",
                "Nenhum processo no SGA", None)

    linhas, bov_vig, bov_venc = [], 0, 0
    def chave(i):
        d = _dias(i.get("dtValidadeFormatado"))
        return (0 if _eh_bovino(i) else 1, -(d if d is not None else -99999))
    for it in sorted(itens, key=chave):
        dias = _dias(it.get("dtValidadeFormatado"))
        sit = _situacao(dias)
        if _eh_bovino(it):
            if dias is not None and dias >= 0:
                bov_vig += 1
            elif dias is not None:
                bov_venc += 1
        atv = it.get("descAtividade") or "?"
        esp = it.get("descAtividadeEspecifica") or ""
        if esp and esp != atv:
            atv = atv + " — " + esp
        doc = it.get("numDocumentoFormatado") or "sem número"
        proto = it.get("numProtocoloFormatado") or it.get("numProtocolo") or "?"
        partes = [
            f"• {it.get('siglaModalidade') or '?'} {doc} — {atv}",
            f"  Protocolo {proto} · {it.get('municipioUfFormatado') or ''}",
            f"  Emitida {it.get('dtDecisaoFormatado') or '—'} · validade "
            f"{it.get('dtValidadeFormatado') or '—'} ({sit})",
        ]
        if it.get("numProtocolo"):
            partes.append("  " + LINK_PUBLICO.format(p=it["numProtocolo"]))
        linhas.append("\n".join(partes))

    resumo = f"{len(itens)} processo(s) no SGA"
    aviso = None
    if bov_vig:
        resumo += f" · {bov_vig} de bovinocultura vigente(s)"
        aviso = ("Já existe licença/dispensa de BOVINOCULTURA vigente para este CPF/CNPJ: "
                 "avaliar se o caso é renovação, ampliação ou outra propriedade antes de "
                 "protocolar uma DLAM nova.")
    elif bov_venc:
        resumo += f" · {bov_venc} de bovinocultura vencida(s)"
        aviso = ("Há licença/dispensa de BOVINOCULTURA VENCIDA para este CPF/CNPJ: o "
                 "pedido pode ser de renovação ou regularização (conferir o protocolo antigo).")
    return "\n\n".join(linhas), resumo, aviso


# --------------------------------------------------------------------------
# Odoo
# --------------------------------------------------------------------------

class Odoo:
    def __init__(self, url, db, login, chave):
        self.db, self.chave = db, chave
        comum = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        self.uid = comum.authenticate(db, login, chave, {})
        if not self.uid:
            raise SystemExit("[ERRO] autenticação no Odoo falhou")
        self.m = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)

    def call(self, modelo, metodo, *args, **kw):
        return self.m.execute_kw(self.db, self.uid, self.chave, modelo, metodo, list(args), kw)


def documento_do_processo(odoo, proc):
    """CPF/CNPJ do empreendedor (cadastro do Empreendimento) ou do cliente do Processo."""
    parceiro_id = None
    if proc.get("x_empreendimento_id"):
        emp = odoo.call("x_empreendimento", "read", [proc["x_empreendimento_id"][0]],
                        fields=["x_empreendedor_id"])
        if emp and emp[0].get("x_empreendedor_id"):
            parceiro_id = emp[0]["x_empreendedor_id"][0]
    if not parceiro_id and proc.get("partner_id"):
        parceiro_id = proc["partner_id"][0]
    if not parceiro_id:
        return None
    p = odoo.call("res.partner", "read", [parceiro_id], fields=["vat"])
    d = so_digitos(p[0].get("vat") if p else "")
    return d if len(d) in (11, 14) else None


def processar(odoo, limite=30, get=requests.get, pausa=PAUSA_SEGUNDOS):
    procs = odoo.call(
        "project.project", "search_read",
        [["company_id", "=", COMPANY_ID], ["x_empreendimento_id", "!=", False],
         "|", ["x_sga_consultado_em", "=", False], ["x_sga_consultar", "=", True]],
        fields=["x_empreendimento_id", "partner_id", "x_sga_situacao"], limit=limite)
    print(f"{len(procs)} processo(s) para consultar")
    feitos = 0
    for proc in procs:
        pid = proc["id"]
        doc = documento_do_processo(odoo, proc)
        if not doc:
            if proc.get("x_sga_situacao") != "Aguardando CPF/CNPJ do empreendedor":
                odoo.call("project.project", "write", [pid],
                          {"x_sga_situacao": "Aguardando CPF/CNPJ do empreendedor"})
            print(f"processo {pid}: sem CPF/CNPJ válido ainda")
            continue
        try:
            itens = consultar_documento(doc, get=get)
        except ErroSGA as e:
            print(f"processo {pid}: {e} — tenta na próxima rodada")
            if "sem resposta" in str(e):
                break  # SGA fora do ar: não insiste nos demais
            continue
        texto, resumo, aviso = montar_resultado(itens)
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        odoo.call("project.project", "write", [pid], {
            "x_sga_licencas": texto, "x_sga_situacao": resumo,
            "x_sga_consultado_em": agora, "x_sga_consultar": False,
        })
        corpo = "Consulta ao SGA/IAT pelo CPF/CNPJ do empreendedor: " + resumo + "."
        if aviso:
            corpo += " ATENÇÃO: " + aviso
        corpo += " Detalhes na aba Anamnese e Mapa."
        odoo.call("project.project", "message_post", [pid], body=corpo)
        feitos += 1
        print(f"processo {pid}: {len(itens)} processo(s) no SGA{' + aviso' if aviso else ''}")
        time.sleep(pausa)
    print(f"concluído: {feitos} consultado(s)")
    return feitos


def main():
    odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"],
                os.environ["ODOO_EMAIL"], os.environ["ODOO_API_KEY"])
    processar(odoo, limite=int(os.environ.get("SGA_LIMITE") or 30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
