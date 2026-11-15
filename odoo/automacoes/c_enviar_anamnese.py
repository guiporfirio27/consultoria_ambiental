# Ação de servidor "Anamnese: enviar ao cliente" (modelo project.project,
# botão "Enviar anamnese" na tela do Processo e menu Ações).
# Cópia versionada do código que roda no Odoo.
#
# Cria a resposta da anamnese já ligada ao Processo (x_processo_id), guarda o
# link pessoal do cliente no Processo, registra no histórico e, se o cliente
# tiver e-mail, envia o convite por e-mail. O link também fica pronto para
# colar no WhatsApp.
#
# Se já houver um convite em aberto (não concluído), reaproveita o mesmo link
# em vez de criar outro. O link enviado abre primeiro a página do mapa
# (docs/mapa/ no GitHub Pages) e dela segue para o questionário.

ANAMNESE_SURVEY_ID = 2
PAGINA_MAPA = 'https://guiporfirio27.github.io/consultoria_ambiental/mapa/'

base = env['ir.config_parameter'].sudo().get_param('web.base.url') or ''

for proc in records:
    emp = proc.x_empreendimento_id
    parceiro = emp.x_empreendedor_id or proc.partner_id
    if not parceiro:
        proc.message_post(body='Anamnese não enviada: o Processo não tem empreendedor nem cliente.')
        continue

    resp = proc.x_anamnese_resposta_id
    if not resp or resp.state == 'done' or resp.partner_id != parceiro:
        resp = env['survey.user_input'].sudo().create({
            'survey_id': ANAMNESE_SURVEY_ID,
            'partner_id': parceiro.id,
            'x_processo_id': proc.id,
        })
    link_survey = base + '/survey/start/' + resp.survey_id.access_token + '?answer_token=' + resp.access_token
    # O cliente recebe o link da PÁGINA DO MAPA (GitHub Pages): marca a
    # estrutura principal, a coordenada vai direto para o Odoo (automação 25,
    # webhook) e a página o leva ao questionário. "Pular" vai direto ao questionário.
    link = (PAGINA_MAPA + '?t=' + resp.access_token + '&s=' + resp.survey_id.access_token)
    coord_ini = (emp.x_coordenada or '').replace(' ', '')
    if coord_ini and len(coord_ini.split(',')) == 2:
        link = link + '&c=' + coord_ini

    proc.write({'x_anamnese_resposta_id': resp.id, 'x_anamnese_link': link,
                'x_anamnese_status': 'enviada'})

    primeiro_nome = (parceiro.name or '').split(' ')[0].title()
    mensagem = ('Olá, ' + primeiro_nome + '! Para montar o seu processo de Dispensa de '
                'Licenciamento Ambiental (DLAM), abra este link: ' + link + ' . Primeiro você '
                'marca no mapa onde fica a sala de ordenha ou o barracão (se estiver na '
                'propriedade, é só tocar em "Estou na propriedade"); depois responde a ficha '
                'da atividade leiteira (uns 5 minutos). Os documentos e as fotos você manda '
                'pelo WhatsApp da GP Consultoria Ambiental. Obrigado!')

    if parceiro.email:
        proc.message_post(body=mensagem, partner_ids=[parceiro.id],
                          message_type='comment', subtype_xmlid='mail.mt_comment')
        proc.message_post(body='Anamnese enviada por e-mail a ' + parceiro.name
                          + '. Para o WhatsApp, copie o campo "Link da anamnese do cliente". '
                          'Link direto do questionário (sem o mapa): ' + link_survey)
    else:
        proc.message_post(body='Anamnese criada para ' + parceiro.name + ' (sem e-mail cadastrado). '
                          'Envie pelo WhatsApp: ' + mensagem)
