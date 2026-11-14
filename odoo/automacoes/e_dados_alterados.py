# Ação das automações "Dados do mapa alterados" — uma por modelo:
#   x_empreendimento (coordenada, área construída, faixa de APP, imóvel, empreendedor)
#   x_imovel         (número do CAR, denominação, localidade, CEP)
#   res.partner      (nome, CPF/CNPJ)
# Cópia versionada do código que roda no Odoo. O mesmo código serve aos três
# modelos: MODELO é trocado na hora de instalar.
#
# Para cada Processo afetado:
#   - mapa "gerado"            -> "desatualizado" + aviso (regerar só com clique);
#   - status vazio/aguardando  -> reconfere os dados (pode ir para a fila).
# O Odoo só dispara quando o VALOR de um dos campos vigiados muda.
# Campos que o próprio gerador do mapa grava (área livre, município, via de
# acesso) não são vigiados, para o mapa não se marcar desatualizado sozinho.

ACAO_VERIFICAR = 1423
MODELO = '__MODELO__'

if MODELO == 'x_empreendimento':
    emps = records
elif MODELO == 'x_imovel':
    emps = env['x_empreendimento'].sudo().search([('x_imovel_id', 'in', records.ids)])
else:
    emps = env['x_empreendimento'].sudo().search([('x_empreendedor_id', 'in', records.ids)])

procs = env['project.project'].sudo().search([
    ('x_empreendimento_id', 'in', emps.ids), ('company_id', '=', 2)])

verificar = []
for proc in procs:
    if proc.x_mapa_status == 'gerado':
        proc.write({'x_mapa_status': 'desatualizado'})
        proc.message_post(body='Dados usados no mapa de situação foram alterados: mapa marcado como '
                          'Desatualizado. Use o botão "Regerar mapa" quando quiser gerar de novo.')
    elif proc.x_mapa_status in (False, 'aguardando'):
        verificar.append(proc.id)

if verificar:
    env['ir.actions.server'].sudo().browse(ACAO_VERIFICAR).with_context(
        active_model='project.project', active_ids=verificar).run()
