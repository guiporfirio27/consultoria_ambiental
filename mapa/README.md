# Mapa de Situação — scripts do QGIS

Esta pasta é a **cópia versionada** dos scripts. Quem roda é a cópia na pasta
`Dados GIS` do computador:

```
D:\Guilherme\Guilherme\Documentos\Dados GIS\
├── rodar_mapa.py            ← você edita só o PROCESSO_ID
├── configurar_credenciais.py← roda uma vez para gravar a chave do Odoo
├── odoo_mapa.py             ← lê/grava no Odoo, confere os dados
├── gerar_mapa_situacao.py   ← monta o mapa (PyQGIS)
├── *.gpkg, logos, rosa dos ventos (base GIS — não vão para o GitHub)
└── saidas\                  ← PDFs gerados (criada sozinha)
```

Credenciais do Odoo (uma vez só): rode no Console Python do QGIS

```python
exec(open(r"D:\Guilherme\Guilherme\Documentos\Dados GIS\configurar_credenciais.py", encoding="utf-8").read())
```

Ele pede a chave de API numa janela (texto oculto, fora do histórico),
testa o login e grava `C:\Users\<seu usuário>\.gp_odoo.json` — fora da pasta
GIS e fora do GitHub.

Rodar (Console Python do QGIS 3.34+ ou 4.x):

```python
exec(open(r"D:\Guilherme\Guilherme\Documentos\Dados GIS\rodar_mapa.py", encoding="utf-8").read())
```

Ao alterar um script aqui, copie para a pasta `Dados GIS` (e vice-versa).
