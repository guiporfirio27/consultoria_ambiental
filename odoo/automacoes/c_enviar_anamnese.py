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
# em vez de criar outro.
#
# Anamnese já concluída: NÃO cria outra (um clique sem querer apagaria a
# resposta do cliente e travaria o mapa). Só avisa no histórico. Para mandar de
# novo de propósito, deixe "Status da anamnese" em branco e clique outra vez.

ANAMNESE_SURVEY_ID = 2

base = env['ir.config_parameter'].sudo().get_param('web.base.url') or ''

for proc in records:
    emp = proc.x_empreendimento_id
    parceiro = emp.x_empreendedor_id or proc.partner_id
    if not parceiro:
        proc.message_post(body='Anamnese não enviada: o Processo não tem empreendedor nem cliente.')
        continue

    resp = proc.x_anamnese_resposta_id
    if proc.x_anamnese_status == 'concluida' and resp and resp.state == 'done':
        d = str(resp.write_date or '')[:10]
        quando = (d[8:10] + '/' + d[5:7] + '/' + d[0:4]) if d else 'data não registrada'
        proc.message_post(body='Anamnese NÃO reenviada: já foi concluída pelo cliente em ' + quando
                          + '. Para mandar de novo, deixe o campo "Status da anamnese" em branco '
                          '(aba Anamnese e Mapa) e clique em "Enviar anamnese" outra vez.')
        continue
    if not resp or resp.state == 'done' or resp.partner_id != parceiro:
        resp = env['survey.user_input'].sudo().create({
            'survey_id': ANAMNESE_SURVEY_ID,
            'partner_id': parceiro.id,
            'x_processo_id': proc.id,
        })
    link = base + '/survey/start/' + resp.survey_id.access_token + '?answer_token=' + resp.access_token

    proc.write({'x_anamnese_resposta_id': resp.id, 'x_anamnese_link': link,
                'x_anamnese_status': 'enviada'})

    primeiro_nome = (parceiro.name or '').split(' ')[0].title()
    mensagem = ('Olá, ' + primeiro_nome + '! Para montar o seu processo de Dispensa de '
                'Licenciamento Ambiental (DLAM), responda a ficha da atividade leiteira '
                '(uns 5 minutos): ' + link + ' . Os documentos e as fotos você manda pelo '
                'WhatsApp da GP Consultoria Ambiental. Obrigado!')

    if parceiro.email:
        proc.message_post(body=mensagem, partner_ids=[parceiro.id],
                          message_type='comment', subtype_xmlid='mail.mt_comment')
        proc.message_post(body='Anamnese enviada por e-mail a ' + parceiro.name
                          + '. Para o WhatsApp, copie o campo "Link da anamnese do cliente".')
    else:
        proc.message_post(body='Anamnese criada para ' + parceiro.name + ' (sem e-mail cadastrado). '
                          'Envie pelo WhatsApp: ' + mensagem)
