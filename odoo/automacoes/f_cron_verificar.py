# Ação agendada "Mapa: reconferir processos aguardando" (a cada 1 hora).
# Cópia versionada do código que roda no Odoo (ir.cron, modelo project.project).
#
# Rede de segurança: se alguma automação não disparar (ex.: documento
# confirmado por um caminho que não altera campo vigiado), a cada hora os
# Processos em "aguardando" ou sem status são reconferidos.

ACAO_VERIFICAR = 1423

procs = model.search([('company_id', '=', 2), ('x_empreendimento_id', '!=', False),
                      ('x_mapa_status', 'in', [False, 'aguardando'])])
if procs:
    env['ir.actions.server'].sudo().browse(ACAO_VERIFICAR).with_context(
        active_model='project.project', active_ids=procs.ids).run()
