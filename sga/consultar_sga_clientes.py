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
    # Mesmas faixas da varredura regional (90/180/365 dias).
    if dias is None:
        return "sem validade informada"
    if dias < 0:
        return f"VENCIDA há {abs(dias)} dias"
    if dias <= 90:
        return f"vigente, vence em {dias} dias (até 90)"
    if dias <= 180:
        return f"vigente, vence em {dias} dias (até 180)"
    if dias <= 365:
        return f"vigente, vence em {dias} dias (até 365)"
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


# --------------------------------------------------------------------------
# Diagnóstico: quais campos o SGA devolve (só NOMES, nunca valores)
# --------------------------------------------------------------------------

def _rotulos_da_pagina(html):
    """Rótulos da página pública do protocolo: textos curtos que terminam em
    ':' (ex.: 'Endereço:', 'Telefone:'). Valores ficam de fora."""
    import html as h
    texto = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    pedacos = re.split(r"<[^>]+>", texto)
    vistos = []
    for p in pedacos:
        p = re.sub(r"\s+", " ", h.unescape(p)).strip()
        if 2 <= len(p) <= 45 and p.endswith(":") and not re.search(r"\d{3}", p):
            if p not in vistos:
                vistos.append(p)
    return vistos


def _chaves(obj, prefixo="", saida=None, nivel=0):
    """Nomes de chaves de um JSON (aninhado), sem os valores."""
    saida = [] if saida is None else saida
    if nivel > 4:
        return saida
    if isinstance(obj, dict):
        for k, v in obj.items():
            nome = prefixo + str(k)
            if nome not in saida:
                saida.append(nome)
            _chaves(v, nome + ".", saida, nivel + 1)
    elif isinstance(obj, list) and obj:
        _chaves(obj[0], prefixo.rstrip(".") + "[].", saida, nivel + 1)
    return saida


def _diagnostico_tela(html, proto, get):
    """A página do protocolo é montada no navegador (ExtJS): os dados vêm de
    outras chamadas. Mostra o tamanho da página, os rótulos de campo escritos
    no JavaScript (fieldLabel/header/title — textos fixos da tela, não dados)
    e as ações do SGA que a página chama; cada ação que devolver JSON com o
    protocolo tem os NOMES das chaves impressos, sem valores."""
    print(f"[diagnóstico] página: {len(html)} caracteres, "
          f"{len(re.findall(r'(?i)<script', html))} bloco(s) de script")
    rotulos = []
    for m in re.finditer(r"(?:fieldLabel|header|title|boxLabel)\s*:\s*['\"]([^'\"]{2,45})['\"]", html):
        t = m.group(1).strip()
        if t not in rotulos and not re.search(r"\d{3}", t):
            rotulos.append(t)
    print(f"[diagnóstico] rótulos da tela (JavaScript): {len(rotulos)}")
    for r in rotulos:
        print("   -", r)
    nomes = list(rotulos)
    acoes = []
    for m in re.finditer(r"action=([A-Za-z]{4,60})", html):
        if m.group(1) not in acoes:
            acoes.append(m.group(1))
    print("[diagnóstico] ações chamadas pela página:", ", ".join(acoes) if acoes else "nenhuma")
    for acao in acoes[:12]:
        if acao in ("carregarInterfaceInicial", "exibirDadosPublicoLicenca"):
            continue
        try:
            r = get(BASE_URL, params={"action": acao, "numProtocolo": proto, "indSia": "false",
                                      "_dc": str(int(time.time() * 1000))},
                    headers=HEADERS, timeout=30)
            r.encoding = "ISO-8859-1"
            t = (r.text or "").strip()
            data = None
            for tt in (t, _corrigir_json(t)):
                try:
                    data = json.loads(tt)
                    break
                except ValueError:
                    continue
            if data is None:
                print(f"   ação {acao}: HTTP {r.status_code}, não é JSON ({len(t)} caracteres)")
                continue
            ch = _chaves(data)
            print(f"   ação {acao}: HTTP {r.status_code}, {len(ch)} campo(s):")
            for c in ch[:80]:
                print("      ·", c)
            nomes.extend(ch)
        except Exception as e:
            print(f"   ação {acao}: falhou ({type(e).__name__})")
        time.sleep(0.5)
    return nomes


def diagnostico(municipio, get=requests.get):
    """Consulta UM município (como o varredor do monitor-editais-pr) e imprime
    os nomes dos campos da lista e os rótulos da página pública do 1º
    protocolo. Não imprime nenhum dado de pessoa."""
    params = {
        "action": "consultarProcessoLicenciamento", "_dc": str(int(time.time() * 1000)),
        "estadoMunicipioCombo": "PR", "municipioCombo": municipio,
        "numProtocolo": "", "numDocumento": "", "cpfCnpj": "", "nomeRazaoSocial": "",
        "dtInicial": "", "dtFinal": "", "ckPesquisaAvancada": "on",
        "page": 1, "start": 0, "limit": 5,
    }
    resp = get(BASE_URL, params=params, headers=HEADERS, timeout=40)
    resp.encoding = "ISO-8859-1"
    texto = (resp.text or "").strip()
    data = None
    for t in (texto, _corrigir_json(texto)):
        try:
            data = json.loads(t)
            break
        except ValueError:
            continue
    if not data or not data.get("listaProcessoLicenciamento"):
        print("[diagnóstico] a lista do município veio vazia ou ilegível")
        return
    item = data["listaProcessoLicenciamento"][0]
    print("[diagnóstico] campos da LISTA do SGA:")
    for k in sorted(item):
        print("   -", k)
    proto = item.get("numProtocolo")
    if not proto:
        print("[diagnóstico] item sem numProtocolo: página pública não consultada")
        return
    pag = get(LINK_PUBLICO.format(p=proto), headers=HEADERS, timeout=40)
    pag.encoding = "ISO-8859-1"
    rotulos = _rotulos_da_pagina(pag.text)
    print(f"[diagnóstico] página pública do protocolo: HTTP {pag.status_code}, "
          f"{len(rotulos)} rótulo(s):")
    for r in rotulos:
        print("   -", r)
    extra = _diagnostico_tela(pag.text or "", proto, get)
    palavras = ("telefone", "fone", "celular", "e-mail", "email", "endere", "logradouro",
                "coorden", "latitude", "longitude", "utm", "respons", "técnico", "tecnico")
    achou = [r for r in rotulos + extra if any(p in r.lower() for p in palavras)]
    print("[diagnóstico] rótulos de contato/endereço/localização/técnico:",
          ", ".join(achou) if achou else "nenhum")


def main():
    if os.environ.get("SGA_DIAGNOSTICO", "").lower() in ("1", "true", "sim"):
        try:
            diagnostico(os.environ.get("SGA_DIAGNOSTICO_MUNICIPIO") or "6375")
        except Exception as e:  # o diagnóstico nunca impede a consulta normal
            print(f"[diagnóstico] falhou: {type(e).__name__}")
    odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"],
                os.environ["ODOO_EMAIL"], os.environ["ODOO_API_KEY"])
    processar(odoo, limite=int(os.environ.get("SGA_LIMITE") or 30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
