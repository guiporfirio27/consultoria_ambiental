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

## Versão 3.16 (14/11/2026)
- Círculo tracejado de **50 m** em volta da estrutura principal (IN 23/2026, Anexo II, entorno; art. 68).
- **Edificações do OpenStreetMap** dentro do círculo, desenhadas em laranja. Se o OSM não responder ou não tiver casas, o mapa sai do mesmo jeito e o Processo recebe o aviso para conferir no satélite.
- **Nascentes do IAT** (opcional): qualquer `.gpkg` com "nascente" no nome dentro da pasta de dados, ou `--camada-nascentes`. Desenha o ponto e a APP de 50 m, e informa a distância da estrutura à nascente mais próxima.
- Esterqueira e casas informadas pelo cliente **não** entram no mapa (decisão de 14/11/2026).
- `fila_mapas.py`: com o HD externo desconectado, a rodada não mexe em nada e os processos continuam na fila.
