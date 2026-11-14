# Automações do Odoo — anamnese e mapa de situação

Cópia versionada do código que roda **dentro do Odoo** (ações de servidor,
automações e ação agendada). Ao alterar um arquivo aqui, atualize o campo
`code` do registro correspondente no Odoo (ou peça ao Claude).

| Arquivo | Registro no Odoo | Disparo |
|---|---|---|
| `a_verificar_mapa.py` | Ação de servidor **1423** "Mapa: verificar dados" | Chamada pelas outras |
| `b_gravar_anamnese.py` | Automação **21** (ação 1426) | Resposta da anamnese muda para concluída |
| `c_enviar_anamnese.py` | Ação de servidor **1424** "Anamnese: enviar ao cliente" | Botão "Enviar anamnese" no Processo |
| `d_regerar_mapa.py` | Ação de servidor **1425** "Mapa: regerar" | Botão "Regerar mapa" no Processo |
| `e_dados_alterados.py` | Automações **22** (Empreendimento), **23** (Imóvel), **24** (Contato) | Mudança de valor em campo vigiado |
| `f_cron_verificar.py` | Ação agendada **93** | A cada 1 hora |
| `../acao_1420_confirmar_e_gravar.py` | Ação de servidor **1420** "Confirmar e gravar" | Menu Ações da fila de documentos |

Tela: a visão **5005** acrescenta ao Processo a aba "Anamnese e Mapa" e os
botões "Enviar anamnese" e "Regerar mapa".

## Fluxo

```
Botão "Enviar anamnese" (1424) ─► link pessoal do cliente (e-mail e/ou WhatsApp)
Cliente conclui ─► automação 21: grava no Empreendimento, enquadramento IN 23/2026,
                   alertas ─► "Mapa: verificar dados" (1423)
Documento confirmado / dado alterado ─► automações 22–24 ─► 1423 (ou "Desatualizado")
1423: anamnese + CPF/CNPJ válido + CAR válido ─► status "Na fila"
PC (mapa/fila_mapas.py, Agendador do Windows) ─► gera, anexa, status "Gerado"
```

## Restrições do código (safe_eval do Odoo)

Sem `def`, `lambda`, `import`, f-string ou compreensão de lista; sem atributo
começando com `_`. Os arquivos são conferidos por um teste de AST antes de
subir.
