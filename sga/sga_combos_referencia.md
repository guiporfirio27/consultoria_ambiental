# Modalidades e grupos do SGA/IAT (id + texto)

Lido direto do formulário de consulta pública do SGA
(`consultarProcessoLicenciamento.do`, combos `idModalidade` e
`idGrupoAtividadeLicenca`) em 19/11/2026. É o que alimenta os menus do
workflow `varredura-sga-crm.yml`. Reconferir se o IAT mudar o formulário.

O resultado da consulta traz, por licença, `idModalidade` e `idGrupoAtividade`
— os mesmos ids abaixo —, então a varredura filtra por eles.

## Modalidade (idModalidade)

| id | Texto no SGA |
|----|----|
| 16 | AA - Autorização Ambiental |
| 18 | AF - Autorização Florestal |
| 6  | DLAM - Declaração de Dispensa de Licenciamento Ambiental |
| 23 | DLAM - Dispensa de Licenciamento Ambiental Municipal |
| 29 | LIR - Licença de Instalação de Regularização |
| 26 | LAC - Licença Ambiental por Adesão e Compromisso |
| 27 | RLAC - Renovação de Licença Ambiental por Adesão e Compromisso |
| 7  | LAS - Licença Ambiental Simplificada |
| 10 | LASR - Licença Ambiental Simplificada de Regularização |
| 30 | LAS-A - Licença Ambiental Simplificada de Ampliação |
| 8  | RLAS - Renovação de Licença Ambiental Simplificada |
| 25 | CP - Consulta Prévia |
| 1  | LP - Licença Prévia |
| 2  | LI - Licença de Instalação |
| 4  | RLI - Renovação de Licença de Instalação |
| 3  | LO - Licença de Operação |
| 9  | LOR - Licença de Operação de Regularização |
| 5  | RLO - Renovação de Licença de Operação |
| 11 | LP-A - Licença Prévia (Ampliação) |
| 12 | LI-A - Licença de Instalação (Ampliação) |
| 13 | RLI-A - Renovação de Licença de Instalação (Ampliação) |
| 14 | LO-A - Licença de Operação (Ampliação) |
| 19 | LI-C - Licença de Instalação (Complementar) |
| 21 | LO-C - Licença de Operação (Complementar) |
| 20 | RLI-C - Renovação de Licença de Instalação (Complementar) |

Para a bovinocultura de leite (DLAM), a modalidade é o id **6**.

## Grupo de atividade (idGrupoAtividade)

| id | Texto no SGA |
|----|----|
| 5  | Agropecuária |
| 6  | Comércio e Serviço |
| 24 | Corte Isolado |
| 9  | Destinação final de resíduos sólidos industriais, urbanos ou de serviço de saúde e efluentes |
| 28 | Empreendimentos Náuticos |
| 29 | Empreendimentos Viários |
| 16 | Geração, armazenamento e transmissão de energia |
| 10 | Gerenciamento de resíduos sólidos e/ou efluentes |
| 8  | Imobiliário |
| 1  | Industrial |
| 27 | Substituição de Espécies Exóticas |

A bovinocultura fica no grupo **5 (Agropecuária)**.
