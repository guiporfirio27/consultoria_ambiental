# Ação de servidor "Mapa: verificar dados" (modelo project.project).
# Cópia versionada do código que roda no Odoo (ir.actions.server).
#
# Confere se o Processo tem tudo o que o mapa de situação exige e decide o
# status: "aguardando" (com a lista do que falta) ou "na_fila" (o PC local
# gera na próxima rodada). Só mexe em Processos com status vazio ou
# "aguardando" — Gerado, Desatualizado, Erro, Gerando e Na fila não são
# tocados aqui.
#
# Itens conferidos (plano de 02/10/2026, seção 3.1, com a decisão de que a
# localização da estrutura é opcional — sem ela, o mapa usa o centro do CAR):
#   - anamnese concluída;
#   - empreendedor com CPF/CNPJ válido (dígito verificador);
#   - imóvel com número do CAR no padrão UF-0000000-<32 hex>.
#
# Chamada por: automação da anamnese, automações de alteração de dados,
# ação "Mapa: regerar" e a ação agendada horária.
# Restrições do safe_eval do Odoo: sem def, lambda, import, f-string ou
# compreensão de lista.

for proc in records:
    if proc.company_id.id != 2 or not proc.x_empreendimento_id:
        continue
    if proc.x_mapa_status not in (False, 'aguardando'):
        continue

    pend = []
    emp = proc.x_empreendimento_id

    if proc.x_anamnese_status != 'concluida':
        pend.append('Anamnese ainda não concluída pelo cliente.')

    pessoa = emp.x_empreendedor_id
    if not pessoa:
        pend.append('Empreendimento sem Empreendedor.')
    else:
        dig = ''
        for c in (pessoa.vat or ''):
            if c.isdigit():
                dig = dig + c
        valido = False
        if len(dig) == 11 and dig != dig[0] * 11:
            valido = True
            for n in (9, 10):
                soma = 0
                for i in range(n):
                    soma = soma + int(dig[i]) * (n + 1 - i)
                if (soma * 10) % 11 % 10 != int(dig[n]):
                    valido = False
        elif len(dig) == 14 and dig != dig[0] * 14:
            valido = True
            p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
            p2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
            for pesos, pos in ((p1, 12), (p2, 13)):
                soma = 0
                for i in range(pos):
                    soma = soma + int(dig[i]) * pesos[i]
                dv = 0
                if soma % 11 >= 2:
                    dv = 11 - soma % 11
                if dv != int(dig[pos]):
                    valido = False
        if not valido:
            pend.append('CPF/CNPJ do empreendedor ausente ou inválido (falta o documento pessoal).')

    imovel = emp.x_imovel_id
    if not imovel:
        pend.append('Empreendimento sem Imóvel (falta o recibo do CAR).')
    else:
        car = (imovel.x_numero_car or '').replace(' ', '').upper()
        partes = car.split('-')
        car_ok = (len(partes) == 3 and len(partes[0]) == 2 and partes[0].isalpha()
                  and len(partes[1]) == 7 and partes[1].isdigit() and len(partes[2]) == 32)
        if car_ok:
            for c in partes[2]:
                if c not in '0123456789ABCDEF':
                    car_ok = False
        if not car_ok:
            pend.append('Número do CAR ausente ou fora do padrão (falta o recibo do CAR).')

    if pend:
        texto = ''
        for p in pend:
            texto = texto + '- ' + p + '\n'
        texto = texto.strip()
        if proc.x_mapa_status != 'aguardando' or (proc.x_mapa_pendencias or '') != texto:
            proc.write({'x_mapa_status': 'aguardando', 'x_mapa_pendencias': texto})
    else:
        proc.write({'x_mapa_status': 'na_fila', 'x_mapa_pendencias': False})
        proc.message_post(body='Dados do mapa completos: mapa de situação colocado na fila de geração.')
