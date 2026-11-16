# Ação da automação "Anamnese concluída → cadastros" (modelo survey.user_input).
# Cópia versionada do código que roda no Odoo.
#
# Quando o cliente conclui a anamnese ligada a um Processo:
#   1. grava as respostas no Empreendimento (área, coordenada, sistema,
#      rebanho, trabalho, faixa de APP, estruturas, dejetos e resumo);
#   2. calcula o enquadramento pela IN IAT 23/2026 (Anexo I);
#   3. gera os alertas (IN 23/2026 e Decreto 9.541/2025, art. 56);
#   4. marca a anamnese como concluída e chama "Mapa: verificar dados".
#
# Decisões do Guilherme (14/11/2026): agricultura familiar e gerador a diesel
# NÃO desclassificam a DLAM (teste no SGA) e não geram alerta.
# Restrições do safe_eval: sem def, lambda, import, f-string ou compreensão.

ACAO_VERIFICAR = 1423

mapa_area = {'Própria': 'propria', 'Arrendada': 'arrendada', 'Cedida (comodato)': 'comodato',
             'Em parceria': 'parceria', 'Posse (sem escritura)': 'posse'}
mapa_app = {'Menos de 10 m': '30', 'De 10 a 50 m': '50', 'Mais de 50 m': '100',
            'Não há rio ou córrego por perto': '30'}
limites = {'confinado': [100, 300, 700], 'semiconfinado': [200, 650, 1500]}

for resp in records.sudo():
    proc = resp.x_processo_id
    if not proc or resp.state != 'done':
        continue
    emp = proc.x_empreendimento_id

    # --- respostas por chave estável (x_chave) ---
    esc = {}   # escolhas: chave -> lista de textos
    val = {}   # texto/número: chave -> valor
    for l in resp.user_input_line_ids:
        if l.skipped:
            continue
        chave = l.question_id.x_chave or ('q' + str(l.question_id.id))
        if l.answer_type == 'suggestion':
            lista = esc.get(chave, [])
            lista.append(l.suggested_answer_id.value or '')
            esc[chave] = lista
        elif l.answer_type == 'numerical_box':
            val[chave] = l.value_numerical_box
        elif l.answer_type == 'text_box':
            val[chave] = (l.value_text_box or '').strip()
        elif l.answer_type == 'char_box':
            val[chave] = (l.value_char_box or '').strip()
        elif l.answer_type == 'date':
            val[chave] = str(l.value_date or '')

    # --- resumo legível, na ordem do questionário ---
    resumo = ''
    for q in resp.survey_id.question_ids.sorted('sequence'):
        chave = q.x_chave or ('q' + str(q.id))
        texto = ''
        if chave in esc:
            texto = ', '.join(esc[chave])
        elif chave in val:
            v = val[chave]
            if isinstance(v, float) and v == int(v):
                v = int(v)
            texto = str(v)
        if texto:
            resumo = resumo + q.title + ': ' + texto + '\n'

    sist_txt = esc.get('sistema_criacao', [''])[0]
    sistema = False
    if sist_txt.startswith('Confinado'):
        sistema = 'confinado'
    elif sist_txt.startswith('Semiconfinado'):
        sistema = 'semiconfinado'
    elif sist_txt.startswith('Extensivo'):
        sistema = 'extensivo'
    vacas = int(val.get('vacas_lactacao') or 0)
    vmax = int(val.get('vacas_lactacao_max') or 0)
    area_txt = esc.get('dominialidade', [''])[0]
    # Pergunta única desde 16/11/2026: nº de empregados (0 = só a família).
    tem_emp = 'num_empregados' in val
    n_emp = int(val.get('num_empregados') or 0)
    rio_txt = esc.get('largura_rio', [''])[0]
    coord = val.get('coord_estrutura') or ''

    vals = {}
    if 'estrutura_area_m2' in val:
        vals['x_area_construida_m2'] = float(val['estrutura_area_m2'] or 0)
    if coord:
        vals['x_coordenada'] = coord
    if sistema:
        vals['x_sistema_criacao'] = sistema
        vals['x_tipo_atividade'] = 'Bovinocultura de leite — ' + sistema
    if 'vacas_lactacao' in val:
        vals['x_vacas_lactacao'] = vacas
    if 'animais_total' in val:
        vals['x_animais_total'] = int(val['animais_total'] or 0)
    vals['x_vacas_lactacao_max'] = vmax
    if area_txt in mapa_area:
        vals['x_situacao_area'] = mapa_area[area_txt]
    if tem_emp:
        vals['x_quem_trabalha'] = 'empregados' if n_emp > 0 else 'familia'
        vals['x_num_empregados'] = n_emp
    if rio_txt in mapa_app:
        vals['x_app_faixa_m'] = mapa_app[rio_txt]
    estr = ', '.join(esc.get('estruturas', []))
    desc = val.get('estruturas_descricao') or ''
    if desc:
        estr = (estr + '\n' + desc).strip()
    vals['x_estruturas'] = estr or False
    vals['x_local_aplicacao_dejeto'] = (val.get('local_aplicacao') or '') or False
    vals['x_anamnese_resumo'] = resumo.strip() or False
    if emp:
        emp.write(vals)

    # --- enquadramento (IN 23/2026, Anexo I) ---
    alertas = []
    if sistema == 'extensivo':
        # Leite extensivo não tem faixa no Anexo I da IN 23/2026.
        enq = 'A confirmar no IAT (leite extensivo não consta da IN 23/2026, Anexo I) — ' \
              + str(vacas) + ' vacas em lactação'
        alertas.append('Sistema extensivo declarado: a IN 23/2026 só traz faixas de leite confinado e '
                       'semiconfinado. Confirmar o sistema com o cliente (vaca que recebe ração no cocho '
                       'é semiconfinado) ou consultar o IAT (possível DILA).')
    elif sistema:
        lim = limites[sistema]
        if vacas <= lim[0]:
            enq = 'DLAM (Micro)'
        elif vacas <= lim[1]:
            enq = 'LAC (Pequeno)'
        elif vacas <= lim[2]:
            enq = 'LAS (Médio)'
        else:
            enq = 'LP/LI/LO (Grande ou Excepcional)'
        enq = enq + ' — ' + str(vacas) + ' vacas em lactação, ' + sistema
        if vacas > lim[0]:
            alertas.append('Porte acima do limite da DLAM (' + str(lim[0]) + ' vacas em lactação no '
                           + sistema + ') — IN 23/2026, Anexo I.')
        elif vmax > lim[0]:
            alertas.append('Ampliação prevista para ' + str(vmax) + ' vacas passa do limite da DLAM: '
                           'novo licenciamento quando acontecer (IN 23/2026, art. 12).')
    else:
        enq = 'Indefinido (sistema de criação não informado)'

    # --- alertas (sinalizam, não decidem) ---
    if esc.get('supressao', [''])[0] == 'Sim':
        alertas.append('Corte de vegetação nativa previsto: DLAM não se aplica (Decreto 9.541/2025, '
                       'art. 56, I); exige Autorização Florestal.')
    if n_emp > 10:
        alertas.append('Mais de 10 empregados (Decreto 9.541/2025, art. 56, V) — conferir enquadramento.')
    if esc.get('embargo', [''])[0] == 'Sim':
        alertas.append('Cliente declarou embargo ou auto de infração: a declaração de ausência de '
                       'embargos (IN 23, Anexo III) não pode ser assinada. Ver detalhes na anamnese.')
    if area_txt and area_txt != 'Própria':
        alertas.append('Área ' + area_txt.lower() + ': pedir o contrato de arrendamento ou comodato '
                       '(IN 23/2026, art. 13, I).')
    capta = False
    for f in esc.get('fonte_agua', []):
        if f != 'Rede pública':
            capta = True
    if capta and esc.get('tem_outorga', [''])[0] != 'Sim':
        alertas.append('Captação própria sem outorga ou declaração de uso insignificante (IN 23/2026, '
                       'art. 13, XI) — oportunidade de serviço de Outorga.')
    if not coord:
        alertas.append('Sem localização da estrutura principal: o mapa usará o centro do imóvel no CAR '
                       '— conferir no satélite.')
    if esc.get('tem_nascente', [''])[0] == 'Sim':
        alertas.append('Há nascente, açude ou represa: conferir a APP no mapa.')

    texto_alertas = ''
    for a in alertas:
        texto_alertas = texto_alertas + '- ' + a + '\n'

    proc.write({
        'x_anamnese_status': 'concluida',
        'x_anamnese_resposta_id': resp.id,
        'x_enquadramento': enq,
        'x_alertas': texto_alertas.strip() or False,
    })
    nota = 'Anamnese concluída pelo cliente. Enquadramento: ' + enq + '.'
    if alertas:
        nota = nota + ' Alertas (' + str(len(alertas)) + '): ' + ' | '.join(alertas)
    else:
        nota = nota + ' Sem alertas.'
    proc.message_post(body=nota)

    env['ir.actions.server'].sudo().browse(ACAO_VERIFICAR).with_context(
        active_model='project.project', active_id=proc.id, active_ids=[proc.id]).run()
