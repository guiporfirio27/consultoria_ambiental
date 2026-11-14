# Ação de servidor "Mapa: regerar" (modelo project.project, botão "Regerar
# mapa" na tela do Processo e menu Ações).
# Cópia versionada do código que roda no Odoo.
#
# Para mapa Gerado, Desatualizado ou com Erro: volta para "aguardando" e
# reconfere os dados. Se estiver tudo certo, o Processo vai para a fila e o
# PC gera de novo; se faltar algo, a lista de pendências aparece no Processo.

ACAO_VERIFICAR = 1423

ids = []
for proc in records:
    if proc.x_mapa_status in ('gerado', 'desatualizado', 'erro'):
        proc.write({'x_mapa_status': 'aguardando'})
        proc.message_post(body='Regerar mapa solicitado: dados reconferidos.')
        ids.append(proc.id)
if ids:
    env['ir.actions.server'].sudo().browse(ACAO_VERIFICAR).with_context(
        active_model='project.project', active_ids=ids).run()
