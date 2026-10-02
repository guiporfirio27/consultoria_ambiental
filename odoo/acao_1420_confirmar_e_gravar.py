# Ação de servidor 1420 "Confirmar e gravar" (modelo x_documento_pendente).
# Cópia versionada do código que roda no Odoo. Ao alterar aqui, cole o
# conteúdo no campo "code" da ação 1420 (ou peça ao Claude para atualizar).
#
# Revisão 02/10/2026:
#   - CCIR passa a ter gravação automática no Imóvel (chave: código INCRA;
#     se não achar, procura pela matrícula que consta no CCIR).
#   - CCIR e CADPRO são fontes COMPLEMENTARES (decisão D4): só preenchem
#     campos vazios do Imóvel e nunca gravam município. O município vem
#     sempre da malha do IAT, gravado pelo gerador do mapa.
#   - CCIR que declara o titular como PROPRIETÁRIO preenche o Proprietário
#     do Imóvel, se ainda estiver vazio.

for doc in records:
    if doc.x_status != "aguardando":
        continue

    campos = {}
    for linha in (doc.x_dados_extraidos or "").split("\n"):
        if "=" not in linha:
            continue
        pedaco = linha.split("=", 1)
        chave = pedaco[0].strip()
        valor = pedaco[1].strip()
        if chave and valor and valor != "-":
            campos[chave] = valor

    tipo = doc.x_tipo_documento
    alvo_model = False
    alvo_id = False
    nota = ""

    if tipo == "MAT-IMV" or tipo == "CAR" or tipo == "CADPRO" or tipo == "CCIR":

        if tipo == "MAT-IMV":
            chave_json = "numero_matricula"
            chave_odoo = "x_numero_matricula"
        elif tipo == "CAR":
            chave_json = "numero_car"
            chave_odoo = "x_numero_car"
        elif tipo == "CCIR":
            chave_json = "codigo_imovel_incra"
            chave_odoo = "x_codigo_incra"
        else:
            chave_json = "numero_protocolo"
            chave_odoo = "x_numero_cadpro_protocolo"

        complementar = tipo == "CADPRO" or tipo == "CCIR"
        numero = campos.get(chave_json)
        imovel = doc.x_imovel_id

        if not imovel and numero:
            imovel = env["x_imovel"].search([(chave_odoo, "=", numero)], limit=1)

        # CCIR traz a matrícula: é o elo com o imóvel já cadastrado pela matrícula.
        if not imovel and tipo == "CCIR" and campos.get("numero_matricula"):
            imovel = env["x_imovel"].search([("x_numero_matricula", "=", campos["numero_matricula"])], limit=1)
            if imovel:
                nota = nota + " Imovel encontrado pela matricula do CCIR."

        if not imovel and not numero:
            doc.write({"x_resultado": "Falta o numero do documento e nenhum Imovel foi escolhido. Preencha o campo Imovel, ou acrescente a linha '" + chave_json + " = ...' nos dados extraidos, e confirme de novo."})
            continue

        valores = {}
        if numero:
            valores[chave_odoo] = numero

        # Município só de MAT-IMV e CAR. CADPRO e CCIR nunca gravam município (D4).
        if campos.get("municipio") and (tipo == "MAT-IMV" or tipo == "CAR"):
            valores["x_municipio"] = campos["municipio"]
        if tipo == "MAT-IMV":
            if campos.get("cartorio"):
                valores["x_cartorio"] = campos["cartorio"]
            if campos.get("comarca"):
                valores["x_comarca"] = campos["comarca"]

        if tipo == "CCIR":
            if campos.get("denominacao"):
                valores["x_denominacao"] = campos["denominacao"]
            if campos.get("numero_matricula"):
                valores["x_numero_matricula"] = campos["numero_matricula"]
            if campos.get("cartorio"):
                valores["x_cartorio"] = campos["cartorio"]
            if campos.get("numero_ccir"):
                valores["x_ccir_numero"] = campos["numero_ccir"]
            if campos.get("exercicio"):
                valores["x_ccir_exercicio"] = campos["exercicio"]
            if campos.get("classificacao_fundiaria"):
                valores["x_ccir_classificacao_fundiaria"] = campos["classificacao_fundiaria"]

        if tipo == "CADPRO":
            if campos.get("localidade"):
                valores["x_localidade"] = campos["localidade"]
            if campos.get("cep"):
                valores["x_cep"] = campos["cep"]

        # Números em formato brasileiro ou com ponto decimal.
        numericos = []
        if tipo == "MAT-IMV":
            numericos.append(["area_ha", "x_area_ha"])
        elif tipo == "CAR":
            numericos.append(["area_total_ha", "x_car_area_total_ha"])
        elif tipo == "CCIR":
            numericos.append(["area_total_ha", "x_ccir_area_total_ha"])
            numericos.append(["numero_modulos_fiscais", "x_ccir_modulos_fiscais"])

        for par in numericos:
            bruto_area = campos.get(par[0])
            if not bruto_area:
                continue
            texto_area = bruto_area.replace(" ", "")
            if "," in texto_area:
                texto_area = texto_area.replace(".", "").replace(",", ".")
            area_ok = True
            ja_tem_ponto = False
            if not texto_area:
                area_ok = False
            for caractere in texto_area:
                if caractere == ".":
                    if ja_tem_ponto:
                        area_ok = False
                    ja_tem_ponto = True
                elif not caractere.isdigit():
                    area_ok = False
            if area_ok:
                valores[par[1]] = float(texto_area)
            else:
                nota = nota + " Valor '" + bruto_area + "' (" + par[0] + ") nao reconhecido, nao gravado."

        datas = []
        if tipo == "CAR":
            datas.append(["data_emissao", "x_car_data_emissao"])
        elif tipo == "CCIR":
            datas.append(["data_emissao", "x_ccir_data_emissao"])
        for par in datas:
            bruto_data = campos.get(par[0])
            if bruto_data and len(bruto_data) == 10 and bruto_data[4] == "-" and bruto_data[7] == "-":
                so_numeros = bruto_data.replace("-", "")
                if so_numeros.isdigit():
                    valores[par[1]] = bruto_data

        if imovel:
            if complementar:
                # Fonte complementar: só preenche o que está vazio.
                # Os campos próprios do CCIR (x_ccir_*) são sempre atualizados:
                # um CCIR de exercício mais novo substitui o anterior.
                ignorados = []
                filtrados = {}
                for campo in valores:
                    if campo.startswith("x_ccir_") or not imovel[campo]:
                        filtrados[campo] = valores[campo]
                    elif str(imovel[campo]).strip() != str(valores[campo]).strip():
                        ignorados.append(campo)
                valores = filtrados
                if ignorados:
                    nota = nota + " Mantido o valor ja cadastrado em: " + ", ".join(ignorados) + " (" + tipo + " so completa dados faltantes)."
            imovel.write(valores)
            sufixo = ""
        else:
            valores["x_name"] = numero
            imovel = env["x_imovel"].create(valores)
            sufixo = " (novo)"

        documento_titular = campos.get("documento_titular")
        nome_titular = campos.get("titular")
        pessoa = False

        if documento_titular:
            digitos = ""
            for caractere in documento_titular:
                if caractere.isdigit():
                    digitos = digitos + caractere
            pessoa = env["res.partner"].search([("company_id", "=", 2), ("vat", "in", [documento_titular, digitos])], limit=1)
            precisa_pessoa = not imovel.x_titular_documento_id or (tipo == "CCIR" and not imovel.x_proprietario_id)
            if not pessoa and precisa_pessoa:
                if len(digitos) == 14:
                    nome_tipo_doc = "CNPJ"
                else:
                    nome_tipo_doc = "CPF"
                tipo_doc = env["l10n_latam.identification.type"].search([("name", "=", nome_tipo_doc)], limit=1)
                novos_valores = {"name": nome_titular or ("(sem nome) " + documento_titular), "vat": documento_titular, "company_id": 2, "x_status_cadastro": "pendente_confirmacao"}
                if tipo_doc:
                    novos_valores["l10n_latam_identification_type_id"] = tipo_doc.id
                pessoa = env["res.partner"].create(novos_valores)
                pessoa.write({"is_company": len(digitos) == 14})
                nota = nota + " Titular cadastrado como pendente de confirmacao."
            if pessoa and not imovel.x_titular_documento_id:
                imovel.write({"x_titular_documento_id": pessoa.id})
                nota = nota + " Titular vinculado ao imovel."
            # O CCIR declara a condição do titular. Só "Proprietário" vira
            # proprietário do imóvel; posseiro/arrendatário ficam só como titular.
            condicao = (campos.get("condicao_titular") or "").lower()
            if tipo == "CCIR" and pessoa and "propriet" in condicao and not imovel.x_proprietario_id:
                imovel.write({"x_proprietario_id": pessoa.id})
                nota = nota + " Proprietario preenchido pelo CCIR."
        elif nome_titular:
            nota = nota + " Titular '" + nome_titular + "' veio sem CPF/CNPJ -- vincule a mao se for o caso."

        alvo_model = "x_imovel"
        alvo_id = imovel.id
        destino_nome = "imovel " + (imovel.x_name or str(imovel.id)) + sufixo

    elif tipo == "RG" or tipo == "CNH" or tipo == "CPF":

        pessoa = doc.x_partner_id
        cpf = campos.get("numero_cpf")

        if not pessoa and cpf:
            digitos = ""
            for caractere in cpf:
                if caractere.isdigit():
                    digitos = digitos + caractere
            pessoa = env["res.partner"].search([("company_id", "=", 2), ("vat", "in", [cpf, digitos])], limit=1)
            if not pessoa:
                tipo_doc = env["l10n_latam.identification.type"].search([("name", "=", "CPF")], limit=1)
                novos_valores = {"name": campos.get("nome_completo") or ("(sem nome) " + cpf), "vat": cpf, "company_id": 2, "x_status_cadastro": "pendente_confirmacao"}
                if tipo_doc:
                    novos_valores["l10n_latam_identification_type_id"] = tipo_doc.id
                pessoa = env["res.partner"].create(novos_valores)
                pessoa.write({"is_company": len(digitos) == 14})
                nota = nota + " Contato criado como pendente de confirmacao."

        if not pessoa:
            doc.write({"x_resultado": "Falta o CPF e nenhuma Pessoa foi escolhida. Preencha o campo Pessoa, ou acrescente a linha 'numero_cpf = ...' nos dados extraidos, e confirme de novo."})
            continue

        valores = {}
        if cpf and not pessoa.vat:
            tipo_doc = env["l10n_latam.identification.type"].search([("name", "=", "CPF")], limit=1)
            valores["vat"] = cpf
            if tipo_doc:
                valores["l10n_latam_identification_type_id"] = tipo_doc.id
        if campos.get("numero_rg") and not pessoa.x_cliente_rg_numero:
            valores["x_cliente_rg_numero"] = campos["numero_rg"]
        if campos.get("orgao_emissor") and not pessoa.x_cliente_rg_orgao_emissor:
            valores["x_cliente_rg_orgao_emissor"] = campos["orgao_emissor"]
        if valores:
            era_empresa = pessoa.is_company
            pessoa.write(valores)
            if "vat" in valores and pessoa.is_company != era_empresa:
                pessoa.write({"is_company": era_empresa})

        alvo_model = "res.partner"
        alvo_id = pessoa.id
        destino_nome = "contato " + pessoa.name

    else:
        if doc.x_partner_id:
            alvo_model = "res.partner"
            alvo_id = doc.x_partner_id.id
            destino_nome = "contato " + doc.x_partner_id.name
        elif doc.x_imovel_id:
            alvo_model = "x_imovel"
            alvo_id = doc.x_imovel_id.id
            destino_nome = "imovel " + (doc.x_imovel_id.x_name or str(doc.x_imovel_id.id))
        else:
            doc.write({"x_resultado": "O tipo '" + (tipo or "nao identificado") + "' nao tem gravacao automatica de campos. Escolha a Pessoa ou o Imovel de destino e confirme de novo -- o arquivo sera anexado la."})
            continue
        nota = nota + " Nenhum campo gravado (tipo sem mapeamento automatico)."

    anexos = env["ir.attachment"].search([("res_model", "=", "x_documento_pendente"), ("res_id", "=", doc.id)])
    movidos = 0
    repetidos = 0
    anexos_movidos = []
    for anexo in anexos:
        ja_la = env["ir.attachment"].search_count([("res_model", "=", alvo_model), ("res_id", "=", alvo_id), ("checksum", "=", anexo.checksum)])
        if ja_la:
            repetidos = repetidos + 1
            continue
        anexo.write({"res_model": alvo_model, "res_id": alvo_id})
        anexos_movidos.append(anexo)
        movidos = movidos + 1

    if repetidos:
        nota = nota + " " + str(repetidos) + " anexo(s) ja estavam la, nao duplicados."

    # O anexo (ir.attachment) e o app Documentos (documents.document) sao coisas
    # diferentes: o anexo aparece no rodape do registro, o app Documentos no
    # botao "Documents" do contato e na arvore de pastas. Gravar so o anexo
    # deixava o contador do botao em zero.
    if alvo_model == "res.partner":
        chave_pasta = "gp.documentos.pasta_pessoas"
        dono_id = alvo_id
    else:
        chave_pasta = "gp.documentos.pasta_imoveis"
        dono_id = False
        imovel_alvo = env["x_imovel"].browse(alvo_id)
        if imovel_alvo.x_titular_documento_id:
            dono_id = imovel_alvo.x_titular_documento_id.id

    parametro = env["ir.config_parameter"].search([("key", "=", chave_pasta)], limit=1)
    pasta_id = False
    if parametro and parametro.value and parametro.value.isdigit():
        pasta_id = int(parametro.value)

    fichados = 0
    if pasta_id:
        for anexo in anexos_movidos:
            ficha = env["documents.document"].search([("attachment_id", "=", anexo.id)], limit=1)
            valores_ficha = {"folder_id": pasta_id}
            if dono_id:
                valores_ficha["partner_id"] = dono_id
            if ficha:
                ficha.write(valores_ficha)
            else:
                valores_ficha["name"] = anexo.name
                valores_ficha["attachment_id"] = anexo.id
                env["documents.document"].create(valores_ficha)
            fichados = fichados + 1
        if fichados:
            nota = nota + " " + str(fichados) + " arquivo(s) no app Documentos."
    else:
        nota = nota + " Pasta do app Documentos nao configurada -- arquivo so como anexo."

    valores_doc = {"x_status": "confirmado", "x_resultado": "Gravado no " + destino_nome + ". " + str(movidos) + " anexo(s) movido(s)." + nota}
    if alvo_model == "res.partner":
        valores_doc["x_partner_id"] = alvo_id
    else:
        valores_doc["x_imovel_id"] = alvo_id
    doc.write(valores_doc)
