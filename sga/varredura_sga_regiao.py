"""
Varredura SGA/IAT por região — oportunidades de licenças a vencer para o CRM.
GP Consultoria Ambiental (empresa 2 no Odoo).

Trazida do repositório monitor-editais-pr (consulta_sga_parana_completo.py) e
ligada ao Odoo: além da planilha Excel por município, cria oportunidades no
CRM (crm.lead) para licenças perto de vencer — o empreendedor vai precisar
renovar, e é aí que entra a GP.

MODOS (variável de ambiente SGA_MODO):
  excel  -> só gera a planilha (como no monitor-editais-pr). Não precisa do Odoo.
  crm    -> gera a planilha E cria/atualiza oportunidades no Odoo (padrão).

ALVO:
  SGA_MUNICIPIOS   códigos separados por vírgula (padrão: região de Campo Mourão
                   e Ivaiporã, os 51 do monitor-editais-pr). "TODOS" = Paraná
                   inteiro (demorado).
  SGA_ATIVIDADE    filtro de atividade para o CRM (padrão "bovino"); vazio = todas.
  SGA_JANELA_DIAS  cria oportunidade para licenças que vencem em até N dias,
                   incluindo as já vencidas há até N dias (padrão 365).

CRM (modo crm): ODOO_URL, ODOO_DB, ODOO_EMAIL, ODOO_API_KEY.
Dedup por protocolo+documento em crm.lead.x_sga_protocolo: cada licença vira no
máximo uma oportunidade, mesmo rodando todo dia.

PRIVACIDADE: dados do SGA são públicos. Prospecção B2B/B2A por interesse
legítimo (LGPD) — a 1ª mensagem ao contato deve identificar a GP e oferecer
descadastro. O log não imprime nome nem CPF/CNPJ (contagens apenas).
"""

import os
import re
import sys
import json
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

# Região padrão: os 51 municípios de Campo Mourão e Ivaiporã (monitor-editais-pr).
REGIAO_PADRAO = [
    6289, 6180, 6556, 6462, 5785, 6272, 5902, 6281, 5846, 6361, 6406, 6235,
    5914, 6324, 6793, 5747, 6108, 5788, 6256, 6433, 5915, 6001, 5809, 6059,
    6216, 5843, 6047, 6790, 6507, 6072, 6306, 6758, 6196, 6013, 6221, 6107,
    6263, 5793, 6559, 6113, 6640, 6273, 6061, 5868, 6838, 6338, 6787, 6148,
    6131, 5943, 6375,
]

MUNICIPIOS = {
    5731:"Abatiá",5735:"Adrianópolis",5743:"Agudos do Sul",5746:"Almirante Tamandaré",
    5747:"Altamira do Paraná",5761:"Altônia",6812:"Alto Paraíso",5755:"Alto Paraná",
    5756:"Alto Piquiri",5763:"Alvorada do Sul",5764:"Amaporã",5766:"Ampére",
    5767:"Anahy",5768:"Andirá",5771:"Ângulo",5774:"Antonina",5776:"Antônio Olinto",
    5780:"Apucarana",5783:"Arapongas",5784:"Arapoti",5785:"Arapuã",5788:"Araruna",
    5789:"Araucária",5793:"Ariranha do Ivaí",5796:"Assaí",5797:"Assis Chateaubriand",
    5798:"Astorga",5799:"Atalaia",5804:"Balsa Nova",5805:"Bandeirantes",
    5809:"Barbosa Ferraz",5819:"Barracão",5812:"Barra do Jacaré",5834:"Bela Vista da Caroba",
    5836:"Bela Vista do Paraíso",5842:"Bituruna",5843:"Boa Esperança",
    5845:"Boa Esperança do Iguaçu",5846:"Boa Ventura de São Roque",5852:"Boa Vista da Aparecida",
    5854:"Bocaiúva do Sul",5856:"Bom Jesus do Sul",5862:"Bom Sucesso",5864:"Bom Sucesso do Sul",
    5868:"Borrazópolis",5871:"Braganey",5873:"Brasilândia do Sul",5886:"Cafeara",
    5888:"Cafelândia",5889:"Cafezal do Sul",5892:"Califórnia",5894:"Cambará",5895:"Cambé",
    5897:"Cambira",5902:"Campina da Lagoa",5904:"Campina do Simão",5906:"Campina Grande do Sul",
    5908:"Campo Bonito",5910:"Campo do Tenente",5911:"Campo Largo",5913:"Campo Magro",
    5914:"Campo Mourão",5915:"Cândido de Abreu",5916:"Candói",5918:"Cantagalo",
    5920:"Capanema",5927:"Capitão Leônidas Marques",5933:"Carambeí",5938:"Carlópolis",
    5941:"Cascavel",5942:"Castro",5943:"Catanduvas",5949:"Centenário do Sul",5955:"Cerro Azul",
    5957:"Céu Azul",5958:"Chopinzinho",5959:"Cianorte",5960:"Cidade Gaúcha",5962:"Clevelândia",
    5964:"Colombo",5983:"Colorado",5990:"Congonhinhas",5991:"Conselheiro Mairinck",
    5993:"Contenda",5995:"Corbélia",5996:"Cornélio Procópio",5997:"Coronel Domingos Soares",
    5999:"Coronel Vivida",6001:"Corumbataí do Sul",6009:"Cruzeiro do Iguaçu",
    6011:"Cruzeiro do Oeste",6012:"Cruzeiro do Sul",6008:"Cruz Machado",6013:"Cruzmaltina",
    6015:"Curitiba",6016:"Curiúva",6024:"Diamante do Norte",6023:"Diamante do Oeste",
    6025:"Diamante do Sul",6030:"Dois Vizinhos",6033:"Douradina",6035:"Doutor Camargo",
    6038:"Doutor Ulysses",6046:"Enéas Marques",6047:"Engenheiro Beltrão",
    6049:"Entre Rios do Oeste",6051:"Esperança Nova",6052:"Espigão Alto do Iguaçu",
    6059:"Farol",6061:"Faxinal",6068:"Fazenda Rio Grande",6072:"Fênix",
    6073:"Fernandes Pinheiro",6077:"Figueira",6082:"Floraí",6081:"Flor da Serra do Sul",
    6083:"Floresta",6084:"Florestópolis",6086:"Flórida",6090:"Formosa do Oeste",
    6091:"Foz do Iguaçu",6092:"Foz do Jordão",6093:"Francisco Alves",6094:"Francisco Beltrão",
    6104:"General Carneiro",6107:"Godoy Moreira",6108:"Goioerê",6109:"Goioxim",
    6113:"Grandes Rios",6115:"Guaíra",6116:"Guairaçá",6120:"Guamiranga",6122:"Guapirama",
    6124:"Guaporema",6126:"Guaraci",6130:"Guaraniaçu",6131:"Guarapuava",6133:"Guaraqueçaba",
    6135:"Guaratuba",6145:"Honório Serpa",6147:"Ibaiti",6148:"Ibema",6150:"Ibiporã",
    6152:"Icaraíma",6155:"Iguaraçu",6157:"Iguatu",6161:"Imbaú",6163:"Imbituva",
    6164:"Inácio Martins",6165:"Inajá",6167:"Indianópolis",6172:"Ipiranga",6175:"Iporã",
    6176:"Iracema do Oeste",6178:"Irati",6180:"Iretama",6181:"Itaguajé",6183:"Itaipulândia",
    6184:"Itambaracá",6185:"Itambé",6189:"Itapejara do Oeste",6190:"Itaperuçu",
    6192:"Itaúna do Sul",6194:"Ivaí",6196:"Ivaiporã",6198:"Ivaté",6199:"Ivatuba",
    6201:"Jaboti",6205:"Jacarezinho",6211:"Jaguapitã",6212:"Jaguariaíva",6213:"Jandaia do Sul",
    6216:"Janiópolis",6217:"Japira",6218:"Japurá",6221:"Jardim Alegre",6222:"Jardim Olinda",
    6226:"Jataizinho",6228:"Jesuítas",6230:"Joaquim Távora",6234:"Jundiaí do Sul",
    6235:"Juranda",6236:"Jussara",6238:"Kaloré",6254:"Lapa",6256:"Laranjal",
    6257:"Laranjeiras do Sul",6261:"Leópolis",6263:"Lidianópolis",6264:"Lindoeste",
    6266:"Loanda",6267:"Lobato",6268:"Londrina",6272:"Luiziana",6273:"Lunardelli",
    6274:"Lupionópolis",6279:"Mallet",6281:"Mamborê",6283:"Mandaguaçu",6284:"Mandaguari",
    6286:"Mandirituba",6287:"Manfrinópolis",6288:"Mangueirinha",6289:"Manoel Ribas",
    6297:"Marechal Cândido Rondon",6299:"Maria Helena",6301:"Marialva",6303:"Marilândia do Sul",
    6304:"Marilena",6306:"Mariluz",6308:"Maringá",6309:"Mariópolis",6310:"Maripá",
    6314:"Marmeleiro",6316:"Marquinho",6319:"Marumbi",6320:"Matelândia",6321:"Matinhos",
    6324:"Mato Rico",6325:"Mauá da Serra",6326:"Medianeira",6330:"Mercedes",6331:"Mirador",
    6334:"Miraselva",6335:"Missal",6338:"Moreira Sales",6339:"Morretes",6342:"Munhoz de Mello",
    6349:"Nossa Senhora das Graças",6352:"Nova Aliança do Ivaí",6354:"Nova América da Colina",
    6356:"Nova Aurora",6361:"Nova Cantu",6364:"Nova Esperança",6365:"Nova Esperança do Sudoeste",
    6366:"Nova Fátima",6367:"Nova Laranjeiras",6368:"Nova Londrina",6370:"Nova Olímpia",
    6371:"Nova Prata do Iguaçu",6373:"Nova Santa Bárbara",6374:"Nova Santa Rosa",
    6375:"Nova Tebas",6380:"Novo Itacolomi",6390:"Ortigueira",6392:"Ourizona",
    6393:"Ouro Verde do Oeste",6396:"Paiçandu",6401:"Palmas",6402:"Palmeira",6406:"Palmital",
    6410:"Palotina",6414:"Paraíso do Norte",6416:"Paranacity",6418:"Paranaguá",
    6419:"Paranapoema",6420:"Paranavaí",6426:"Pato Bragado",6427:"Pato Branco",
    6430:"Paula Freitas",6432:"Paulo Frontin",6433:"Peabiru",6438:"Perobal",6439:"Pérola",
    6440:"Pérola do Oeste",6443:"Piên",6445:"Pinhais",6448:"Pinhalão",6446:"Pinhal de São Bento",
    6454:"Pinhão",6458:"Piraí do Sul",6460:"Piraquara",6462:"Pitanga",6463:"Pitangueiras",
    6465:"Planaltina do Paraná",6466:"Planalto",6471:"Ponta Grossa",6472:"Pontal do Paraná",
    6473:"Porecatu",6476:"Porto Amazonas",6477:"Porto Barreiro",6484:"Porto Rico",
    6489:"Porto Vitória",6490:"Prado Ferreira",6491:"Pranchita",6494:"Presidente Castelo Branco",
    6496:"Primeiro de Maio",6498:"Prudentópolis",6838:"Quarto Centenário",6500:"Quatiguá",
    6501:"Quatro Barras",6502:"Quatro Pontes",6504:"Quedas do Iguaçu",6506:"Querência do Norte",
    6507:"Quinta do Sol",6509:"Quitandinha",6510:"Ramilândia",6511:"Rancho Alegre",
    6512:"Rancho Alegre do Oeste",6513:"Realeza",6514:"Rebouças",6517:"Renascença",
    6518:"Reserva",6519:"Reserva do Iguaçu",6523:"Ribeirão Claro",6524:"Ribeirão do Pinhal",
    6527:"Rio Azul",6528:"Rio Bom",6530:"Rio Bonito do Iguaçu",6531:"Rio Branco do Ivaí",
    6532:"Rio Branco do Sul",6543:"Rio Negro",6554:"Rolândia",6556:"Roncador",6558:"Rondon",
    6559:"Rosário do Ivaí",6560:"Sabáudia",6562:"Salgado Filho",6565:"Salto do Itararé",
    6566:"Salto do Lontra",6569:"Santa Amélia",6570:"Santa Cecília do Pavão",
    6572:"Santa Cruz do Monte Castelo",6575:"Santa Fé",6577:"Santa Helena",6578:"Santa Inês",
    6579:"Santa Isabel do Ivaí",6580:"Santa Izabel do Oeste",6581:"Santa Lúcia",
    6587:"Santa Maria do Oeste",6589:"Santa Mariana",6590:"Santa Mônica",6601:"Santana do Itararé",
    6597:"Santa Tereza do Oeste",6598:"Santa Terezinha de Itaipu",6604:"Santo Antônio da Platina",
    6605:"Santo Antônio do Caiuá",6608:"Santo Antônio do Paraíso",6609:"Santo Antônio do Sudoeste",
    6610:"Santo Inácio",6616:"São Carlos do Ivaí",6632:"São Jerônimo da Serra",6633:"São João",
    6639:"São João do Caiuá",6640:"São João do Ivaí",6642:"São João do Triunfo",
    6647:"São Jorge do Ivaí",6646:"São Jorge do Oeste",6648:"São Jorge do Patrocínio",
    6651:"São José da Boa Vista",6652:"São José das Palmeiras",6656:"São José dos Pinhais",
    6670:"São Manoel do Paraná",6674:"São Mateus do Sul",6679:"São Miguel do Iguaçu",
    6687:"São Pedro do Iguaçu",6688:"São Pedro do Ivaí",6689:"São Pedro do Paraná",
    6702:"São Sebastião da Amoreira",6704:"São Tomé",6708:"Sapopema",6709:"Sarandi",
    6711:"Saudade do Iguaçu",6718:"Sengés",6722:"Serranópolis do Iguaçu",6727:"Sertaneja",
    6728:"Sertanópolis",6734:"Siqueira Campos",6737:"Sulina",6742:"Tamarana",6744:"Tamboara",
    6746:"Tapejara",6747:"Tapira",6753:"Teixeira Soares",6754:"Telêmaco Borba",6758:"Terra Boa",
    6760:"Terra Rica",6761:"Terra Roxa",6762:"Tibagi",6763:"Tijucas do Sul",6770:"Toledo",
    6772:"Tomazina",6774:"Três Barras do Paraná",6786:"Tunas do Paraná",6787:"Tuneiras do Oeste",
    6788:"Tupãssi",6790:"Turvo",6793:"Ubiratã",6794:"Umuarama",6796:"União da Vitória",
    6798:"Uniflor",6799:"Uraí",6806:"Ventania",6807:"Vera Cruz do Oeste",6809:"Verê",
    6827:"Virmond",6832:"Vitorino",6834:"Wenceslau Braz",6835:"Xambrê"
}

# --------------------------------------------------------------------------
# SGA (mesmo acesso do monitor-editais-pr, filtrando por município)
# --------------------------------------------------------------------------

def _corrigir_json(texto):
    texto = re.sub(r":\s*'([^']*)'", r': "\1"', texto)
    return re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', texto)


def consultar_pagina(cod, start=0, limite=100, get=requests.get):
    params = {
        "action": "consultarProcessoLicenciamento",
        "_dc": str(int(time.time() * 1000)),
        "estadoMunicipioCombo": "PR", "municipioCombo": cod,
        "numProtocolo": "", "numDocumento": "", "cpfCnpj": "", "nomeRazaoSocial": "",
        "dtInicial": "", "dtFinal": "", "ckPesquisaAvancada": "on",
        "page": (start // limite) + 1, "start": start, "limit": limite,
    }
    resp = get(BASE_URL, params=params, headers=HEADERS, timeout=40)
    resp.encoding = "ISO-8859-1"
    texto = (resp.text or "").strip()
    for t in (texto, _corrigir_json(texto)):
        try:
            return json.loads(t)
        except ValueError:
            continue
    return None


def dias_para_vencimento(dt_str):
    try:
        return (datetime.strptime(dt_str, "%d/%m/%Y").date() - date.today()).days
    except (TypeError, ValueError):
        return None


def status_vencimento(dias):
    # Faixas do monitor-editais-pr (90/180/365 dias).
    if dias is None:
        return "Sem data"
    if dias < 0:
        return "Vencida"
    if dias <= 90:
        return "🔴 Vence em até 90 dias"
    if dias <= 180:
        return "🟡 Vence em até 180 dias"
    if dias <= 365:
        return "🟠 Vence em até 365 dias"
    return "🟢 Vigente"


def formatar_cpf_cnpj(valor):
    valor = re.sub(r"\D", "", valor or "")
    if len(valor) == 11:
        return f"{valor[:3]}.{valor[3:6]}.{valor[6:9]}-{valor[9:]}"
    if len(valor) == 14:
        return f"{valor[:2]}.{valor[2:5]}.{valor[5:8]}/{valor[8:12]}-{valor[12:]}"
    return valor


def processar_municipio(cod, get=requests.get):
    limite, start, todos = 100, 0, []
    while True:
        data = consultar_pagina(cod, start=start, limite=limite, get=get)
        if not data:
            break
        lista = data.get("listaProcessoLicenciamento") or []
        total = int(data.get("total") or 0)
        for item in lista:
            dt_validade = item.get("dtValidadeFormatado", "")
            dias = dias_para_vencimento(dt_validade)
            proto = item.get("numProtocolo", "")
            todos.append({
                "cpf_cnpj": formatar_cpf_cnpj(item.get("cpfCnpj", "")),
                "doc_digitos": re.sub(r"\D", "", item.get("cpfCnpj", "") or ""),
                "protocolo": item.get("numProtocoloFormatado", "") or str(proto),
                "num_protocolo": str(proto),
                "nome": item.get("nomeRazaoSocial", ""),
                "atividade": item.get("descAtividade", ""),
                "atividade_especifica": item.get("descAtividadeEspecifica", ""),
                "municipio": item.get("municipioUfFormatado", ""),
                "modalidade": item.get("siglaModalidade", ""),
                "num_documento": item.get("numDocumentoFormatado", ""),
                "dt_emissao": item.get("dtDecisaoFormatado", ""),
                "dt_validade": dt_validade,
                "dias_para_vencer": dias if dias is not None else "",
                "status": status_vencimento(dias),
                "link": LINK_PUBLICO.format(p=proto) if proto else "",
            })
        start += limite
        if start >= total or not lista:
            break
        time.sleep(0.3)
    return todos


# --------------------------------------------------------------------------
# Excel (igual ao monitor-editais-pr)
# --------------------------------------------------------------------------

def salvar_excel(registros, arquivo):
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font, Alignment
    except ImportError:
        print("[aviso] openpyxl não instalado: planilha não gerada.")
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Todas as Licenças"
    cab = ["CPF / CNPJ", "Protocolo", "Nome / Razão Social", "Atividade",
           "Atividade Específica", "Município/UF", "Modalidade", "Nº Documento",
           "Dt. Emissão", "Dt. Validade", "Dias p/ Vencer", "Status", "Link SGA"]
    hf, ht = PatternFill("solid", fgColor="1F4E79"), Font(color="FFFFFF", bold=True)
    for c, t in enumerate(cab, 1):
        cell = ws.cell(1, c, t)
        cell.fill, cell.font, cell.alignment = hf, ht, Alignment(horizontal="center")
    cores = {
        "Vencida": PatternFill("solid", fgColor="999999"),
        "🔴 Vence em até 90 dias": PatternFill("solid", fgColor="FFCCCC"),
        "🟡 Vence em até 180 dias": PatternFill("solid", fgColor="FFFF99"),
        "🟠 Vence em até 365 dias": PatternFill("solid", fgColor="FFE0B2"),
        "🟢 Vigente": PatternFill("solid", fgColor="CCFFCC"),
        "Sem data": PatternFill("solid", fgColor="EEEEEE"),
    }
    for row, r in enumerate(registros, 2):
        fill = cores.get(r.get("status"), PatternFill("solid", fgColor="FFFFFF"))
        vals = [r["cpf_cnpj"], r["protocolo"], r["nome"], r["atividade"],
                r["atividade_especifica"], r["municipio"], r["modalidade"],
                r["num_documento"], r["dt_emissao"], r["dt_validade"],
                r.get("dias_para_vencer", ""), r["status"]]
        for c, v in enumerate(vals, 1):
            ws.cell(row, c, v).fill = fill
        if r.get("link"):
            cell = ws.cell(row, 13, "Abrir no SGA")
            cell.hyperlink, cell.font, cell.fill = r["link"], Font(color="0000FF", underline="single"), fill
        else:
            ws.cell(row, 13, "").fill = fill
    for c, larg in enumerate([20, 18, 40, 28, 40, 20, 12, 15, 14, 14, 14, 28, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(c)].width = larg
    wb.save(arquivo)


# --------------------------------------------------------------------------
# Odoo CRM
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


def chave_dedup(r):
    # Protocolo + documento: único por licença, estável entre rodadas.
    return (r.get("num_protocolo") or r.get("protocolo") or "?") + "|" + (r.get("num_documento") or "")


def criar_oportunidades(odoo, registros, janela_dias):
    """Cria uma oportunidade por licença que vence em até `janela_dias` (ou
    vencida há até `janela_dias`). Não duplica: pula o que já tem lead com o
    mesmo x_sga_protocolo. Devolve (criadas, puladas)."""
    alvo = []
    for r in registros:
        d = r.get("dias_para_vencer")
        if isinstance(d, int) and -janela_dias <= d <= janela_dias:
            alvo.append(r)
    if not alvo:
        return 0, 0

    chaves = [chave_dedup(r) for r in alvo]
    existentes = set()
    for i in range(0, len(chaves), 80):
        lote = chaves[i:i + 80]
        achados = odoo.call("crm.lead", "search_read",
                            [["x_sga_protocolo", "in", lote]], fields=["x_sga_protocolo"])
        existentes.update(a["x_sga_protocolo"] for a in achados)

    criadas, puladas = 0, 0
    for r in alvo:
        ch = chave_dedup(r)
        if ch in existentes:
            puladas += 1
            continue
        d = r["dias_para_vencer"]
        quando = (f"vencida há {abs(d)} dias" if d < 0 else f"vence em {d} dias")
        atividade = r["atividade"]
        if r["atividade_especifica"] and r["atividade_especifica"] != atividade:
            atividade += " — " + r["atividade_especifica"]
        descricao = (
            f"Oportunidade gerada da varredura do SGA/IAT.\n\n"
            f"Titular: {r['nome']}\nCPF/CNPJ: {r['cpf_cnpj']}\n"
            f"Atividade: {atividade}\nMunicípio: {r['municipio']}\n"
            f"Licença: {r['modalidade']} {r['num_documento']} — {quando} "
            f"(validade {r['dt_validade'] or '—'}).\n"
            f"Protocolo: {r['protocolo']}\n{r['link']}\n\n"
            f"Contato: buscar telefone/e-mail (CNPJ: Receita; a consulta pública "
            f"do SGA não traz contato)."
        )
        odoo.call("crm.lead", "create", {
            "name": f"Renovação {r['modalidade']} — {r['nome']} ({r['municipio']})",
            "type": "lead",
            "partner_name": r["nome"],
            "company_id": COMPANY_ID,
            "x_sga_protocolo": ch,
            "description": descricao,
        })
        criadas += 1
    return criadas, puladas


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------

def municipios_alvo():
    bruto = (os.environ.get("SGA_MUNICIPIOS") or "").strip()
    if bruto.upper() == "TODOS":
        return dict(MUNICIPIOS)
    if bruto:
        cods = [int(c) for c in re.split(r"[,\s]+", bruto) if c.strip().isdigit()]
    else:
        cods = REGIAO_PADRAO
    return {c: MUNICIPIOS.get(c, f"Município {c}") for c in cods}


def main():
    modo = (os.environ.get("SGA_MODO") or "crm").lower()
    filtro_atv = os.environ.get("SGA_ATIVIDADE", "bovino").strip().lower()
    janela = int(os.environ.get("SGA_JANELA_DIAS") or 365)
    municipios = municipios_alvo()

    print(f"Varredura SGA — {len(municipios)} município(s), modo={modo}, "
          f"filtro atividade='{filtro_atv or 'todas'}', janela={janela} dias")

    todos, erros = [], []
    for i, (cod, nome) in enumerate(municipios.items(), 1):
        try:
            regs = processar_municipio(cod)
            todos.extend(regs)
            alerta = sum(1 for r in regs if "Vence" in r.get("status", ""))
            print(f"  [{i:03d}/{len(municipios)}] {nome}: {len(regs)} licenças, {alerta} com alerta")
            time.sleep(PAUSA_SEGUNDOS)
        except Exception as e:
            print(f"  [{i:03d}/{len(municipios)}] {nome}: ERRO ({type(e).__name__})")
            erros.append(cod)
            time.sleep(3)

    ordem = {"🔴 Vence em até 90 dias": 0, "🟡 Vence em até 180 dias": 1,
             "🟠 Vence em até 365 dias": 2, "Vencida": 3, "🟢 Vigente": 4, "Sem data": 5}
    todos.sort(key=lambda x: ordem.get(x.get("status"), 9))
    print(f"\nTotal: {len(todos)} licenças" + (f" | erros em {len(erros)} município(s)" if erros else ""))

    hoje = date.today().strftime("%Y%m%d")
    arquivo = f"licencas_regiao_{len(municipios)}mun_{hoje}.xlsx"
    salvar_excel(todos, arquivo)
    print(f"Planilha: {arquivo}")

    if modo != "crm":
        return 0

    para_crm = todos
    if filtro_atv:
        para_crm = [r for r in todos
                    if filtro_atv in ((r["atividade"] + " " + r["atividade_especifica"]).lower())]
    odoo = Odoo(os.environ["ODOO_URL"], os.environ["ODOO_DB"],
                os.environ["ODOO_EMAIL"], os.environ["ODOO_API_KEY"])
    criadas, puladas = criar_oportunidades(odoo, para_crm, janela)
    print(f"CRM: {criadas} oportunidade(s) criada(s), {puladas} já existia(m) "
          f"(de {len(para_crm)} licença(s) no filtro).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
