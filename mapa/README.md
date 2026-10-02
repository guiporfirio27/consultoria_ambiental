# Mapa de Situação — scripts do QGIS

Esta pasta é a **cópia versionada** dos scripts. Quem roda é a cópia na pasta
`Dados GIS` do computador:

```
D:\Guilherme\Guilherme\Documentos\Dados GIS\
├── rodar_mapa.py            ← você edita só o PROCESSO_ID
├── odoo_mapa.py             ← lê/grava no Odoo, confere os dados
├── gerar_mapa_situacao.py   ← monta o mapa (PyQGIS)
├── *.gpkg, logos, rosa dos ventos (base GIS — não vão para o GitHub)
└── saidas\                  ← PDFs gerados (criada sozinha)
```

Credenciais do Odoo **fora** da pasta GIS e fora do GitHub:
`C:\Users\<seu usuário>\.gp_odoo.json`

```json
{
  "url": "https://gp-construcaoengenharia.odoo.com",
  "db": "<nome do banco>",
  "email": "guiporfirio27@gmail.com",
  "api_key": "<chave API_mapa_qgis>"
}
```

Rodar (Console Python do QGIS 3.34+ ou 4.x):

```python
exec(open(r"D:\Guilherme\Guilherme\Documentos\Dados GIS\rodar_mapa.py", encoding="utf-8").read())
```

Ao alterar um script aqui, copie para a pasta `Dados GIS` (e vice-versa).
