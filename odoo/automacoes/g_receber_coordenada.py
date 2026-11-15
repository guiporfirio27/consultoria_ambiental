# Ação da automação "Mapa do cliente → coordenada" (gatilho: webhook,
# modelo survey.user_input). Cópia versionada do código que roda no Odoo.
#
# A página docs/mapa/ (GitHub Pages) manda, por POST de formulário:
#   t   = access_token da resposta da anamnese (só quem recebeu o link tem)
#   lat = latitude, lon = longitude (graus decimais, WGS84)
#   origem = 'gps' ou 'clique'
# O "record_getter" da automação acha a resposta pelo token:
#   model.sudo().search([('access_token', '=', payload.get('t') or '-'),
#                        ('survey_id', '=', 2)], limit=1)
# Token errado = nenhum registro = o Odoo rejeita a chamada antes deste código.
#
# Aqui: confere se o ponto cai no Paraná, grava na resposta e, se a anamnese
# já estiver ligada a um Empreendimento, grava lá também (a coordenada marcada
# no mapa vale mais que o centro do CAR e que um texto colado).
# Restrições do safe_eval: sem def, lambda, import, f-string ou compreensão.

dados = {}
try:
    dados = payload or {}
except Exception:
    dados = env.context.get('payload') or {}

lat = False
lon = False
try:
    lat = float(str(dados.get('lat') or '').replace(',', '.'))
    lon = float(str(dados.get('lon') or '').replace(',', '.'))
except Exception:
    lat = False

# Caixa do Paraná com folga: fora disso é erro de clique ou chamada indevida.
dentro = lat is not False and -27.0 <= lat <= -22.3 and -54.9 <= lon <= -47.9

for resp in records:
    if not dentro:
        if resp.x_processo_id:
            resp.x_processo_id.message_post(
                body='Mapa do cliente: coordenada recebida fora do Paraná e ignorada ('
                     + str(dados.get('lat')) + ', ' + str(dados.get('lon')) + ').')
        continue
    coord = ('%.6f, %.6f' % (lat, lon))
    resp.write({'x_coord_mapa': coord, 'x_coord_mapa_em': datetime.datetime.now()})
    proc = resp.x_processo_id
    if not proc:
        continue
    origem = 'GPS do celular' if dados.get('origem') == 'gps' else 'clique no mapa'
    emp = proc.x_empreendimento_id
    if emp:
        emp.write({'x_coordenada': coord})
    proc.message_post(body='Cliente marcou a estrutura principal no mapa (' + origem + '): ' + coord
                      + '. https://www.google.com/maps?q=' + coord.replace(' ', '')
                      + ' — conferir no satélite.')
