# Prompts de classificação e extração de documentos — GP Consultoria Ambiental

Este arquivo é a fonte de verdade dos prompts usados por `process_document.py`.
Mantenha os prompts aqui, não hardcoded no script — segue o mesmo padrão do
catálogo de campos docxtpl (`padrao-campos-docxtpl-gp-consultoria.md`).

Todos os prompts recebem a página do documento **como imagem** (PDF nativo com
camada de texto pula direto para extração via texto, sem passar por aqui —
ver lógica no script).

---

## 1. Prompt de classificação (roda primeiro, sempre)

```
Você vai receber a imagem de uma página de documento. Classifique-a em UM dos
seguintes tipos, baseado exclusivamente no conteúdo visual da página (nunca no
nome do arquivo, que você não recebe):

- RG (carteira de identidade, frente ou verso)
- CNH (Carteira Nacional de Habilitação — traz RG, CPF e filiação num único documento)
- CPF (comprovante de inscrição no CPF)
- MAT-IMV (matrícula de imóvel, documento de cartório de registro de imóveis)
- CAR (recibo do Cadastro Ambiental Rural — SICAR)
- CADPRO (comprovante de Cadastro de Produtor Rural)
- CCIR (Certificado de Cadastro de Imóvel Rural — INCRA/SNCR)
- COMP-RES (comprovante de residência — conta de luz, água, telefone, etc.)
- NAO_IDENTIFICADO (não se encaixa em nenhum tipo acima, ou está ilegível)

Responda APENAS com um JSON válido, sem texto antes ou depois:

{
  "tipo_documento": "RG | CNH | CPF | MAT-IMV | CAR | CADPRO | CCIR | COMP-RES | NAO_IDENTIFICADO",
  "confianca": 0-100,
  "motivo": "justificativa breve (máx. 20 palavras) baseada no que está visível na página"
}

Regras de confiança:
- confianca >= 85: elementos característicos do tipo estão nítidos e completos.
- confianca 50-84: tipo provável, mas com ambiguidade (baixa qualidade de imagem,
  corte incompleto, reflexo cobrindo parte do texto).
- confianca < 50: classifique como NAO_IDENTIFICADO.
```

---

## 2. Prompts de extração (roda só depois da classificação, um por tipo)

### RG
```
Extraia os dados desta carteira de identidade (RG). Responda apenas com JSON:
{
  "nome_completo": "string ou null",
  "numero_rg": "string ou null",
  "orgao_emissor": "string ou null",
  "data_nascimento": "AAAA-MM-DD ou null",
  "filiacao_mae": "string ou null",
  "filiacao_pai": "string ou null",
  "confianca": 0-100
}
Se um campo não estiver legível ou não aparecer nesta página (ex.: frente sem
filiação), retorne null para ele — nunca invente ou complete um valor.
```

### CNH (Carteira Nacional de Habilitação)
```
Extraia os dados desta CNH. Ela costuma trazer RG, CPF e filiação juntos
-- extraia todos os campos presentes. Responda apenas com JSON:
{
  "nome_completo": "string ou null",
  "numero_registro_cnh": "string ou null",
  "numero_rg": "string ou null (campo '4c DOC. IDENTIDADE')",
  "orgao_emissor": "string ou null (órgão + UF do campo '4c')",
  "numero_cpf": "string ou null (campo '4d CPF')",
  "data_nascimento": "AAAA-MM-DD ou null",
  "filiacao_mae": "string ou null",
  "filiacao_pai": "string ou null",
  "confianca": 0-100
}
Se um campo não estiver legível, retorne null -- nunca invente ou complete
um valor.
```

### CPF
```
Extraia os dados deste comprovante de inscrição no CPF. Responda apenas com JSON:
{
  "nome_completo": "string ou null",
  "numero_cpf": "string ou null (formato 000.000.000-00)",
  "confianca": 0-100
}
```

### MAT-IMV (matrícula de imóvel)
```
Extraia os dados desta matrícula de imóvel. Responda apenas com JSON:
{
  "numero_matricula": "string ou null",
  "cartorio": "string ou null",
  "comarca": "string ou null",
  "area_ha": "number ou null",
  "proprietario": "string ou null",
  "confianca": 0-100
}
```

### CAR (recibo do Cadastro Ambiental Rural)
```
Extraia os dados deste recibo do CAR (SICAR). Responda apenas com JSON:
{
  "numero_car": "string ou null (formato UF-CCCCCCC-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX)",
  "municipio": "string ou null",
  "uf": "string ou null",
  "area_total_ha": "number ou null",
  "titular": "string ou null",
  "data_emissao": "AAAA-MM-DD ou null",
  "confianca": 0-100
}
```

### CADPRO
```
Extraia os dados deste comprovante de Cadastro de Produtor Rural. Responda
apenas com JSON:
{
  "numero_protocolo": "string ou null",
  "titular": "string ou null",
  "documento_titular": "string ou null",
  "municipio": "string ou null",            // não é gravado (D4)
  "localidade": "string ou null",           // da propriedade; só preenche se vazio
  "cep": "string ou null",                  // da propriedade; só preenche se vazio
  "atividade_declarada": "string ou null",
  "data_emissao": "AAAA-MM-DD ou null",
  "confianca": 0-100
}
```

### CCIR (Certificado de Cadastro de Imóvel Rural)

Incluído em 02/10/2026. O CCIR traz o **código do imóvel rural (INCRA/SNCR)**,
exigido no cadastro do CAR, e liga o imóvel à **matrícula** e ao
**proprietário**. É fonte **complementar** (decisão D4): só preenche campos
vazios do Imóvel e nunca grava município (que vem sempre da malha do IAT).
Texto exato do prompt: `PROMPTS_EXTRACAO["CCIR"]` em `process_document.py`.

```
{
  "codigo_imovel_incra": "string ou null",   // ex.: 950.068.123.456-7
  "numero_ccir": "string ou null",
  "exercicio": "string ou null",
  "denominacao": "string ou null",
  "municipio": "string ou null",
  "uf": "string ou null",
  "area_total_ha": "number ou null",
  "classificacao_fundiaria": "string ou null",
  "modulo_fiscal_ha": "number ou null",
  "numero_modulos_fiscais": "number ou null",
  "fracao_minima_parcelamento_ha": "number ou null",
  "numero_matricula": "string ou null",      // primeira matrícula
  "cartorio": "string ou null",
  "outras_matriculas": "string ou null",     // "nº (cartório); nº (cartório)"
  "titular": "string ou null",               // declarante ou maior %
  "documento_titular": "string ou null",
  "condicao_titular": "string ou null",      // Proprietário, Posseiro...
  "percentual_detencao": "number ou null",
  "outros_titulares": "string ou null",      // "Nome (CPF, condição, %); ..."
  "data_emissao": "AAAA-MM-DD ou null",
  "confianca": 0-100
}
```

Gravação (`write_to_odoo.py` e ação 1420 do Odoo):

| Campo extraído | Campo no Odoo (`x_imovel`) | Regra |
|---|---|---|
| codigo_imovel_incra | `x_codigo_incra` | chave de busca do imóvel |
| numero_matricula | `x_numero_matricula` | chave alternativa (liga ao imóvel já cadastrado pela matrícula); só preenche se vazio |
| denominacao, cartorio | `x_denominacao`, `x_cartorio` | só preenche se vazio |
| numero_ccir, exercicio, area_total_ha, numero_modulos_fiscais, classificacao_fundiaria, data_emissao | `x_ccir_numero`, `x_ccir_exercicio`, `x_ccir_area_total_ha`, `x_ccir_modulos_fiscais`, `x_ccir_classificacao_fundiaria`, `x_ccir_data_emissao` | sempre atualiza (CCIR novo substitui o anterior) |
| titular + documento_titular | `x_titular_documento_id` | se vazio |
| condicao_titular = "Proprietário" | `x_proprietario_id` | se vazio |
| municipio | — | **não grava** (D4) |

### COMP-RES (comprovante de residência)
```
Extraia os dados deste comprovante de residência. Responda apenas com JSON:
{
  "nome_titular": "string ou null",
  "logradouro": "string ou null",
  "numero": "string ou null",
  "bairro": "string ou null",
  "municipio": "string ou null",
  "uf": "string ou null",
  "cep": "string ou null",
  "tipo_comprovante": "luz | agua | telefone | outro | null",
  "data_emissao": "AAAA-MM-DD ou null",
  "confianca": 0-100
}
```

---

## Regra de corte (aplicada no script, não no prompt)

- `confianca` da classificação OU da extração < 70 → nunca grava no Odoo
  automaticamente. Vai para a fila de revisão humana com o motivo anexado.
- Documento classificado como `NAO_IDENTIFICADO` → mesma fila, sem tentativa
  de extração.
