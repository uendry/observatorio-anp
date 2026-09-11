"""
coletar.py — coleta mensal das bases públicas da ANP.

Roda no dia 5 de cada mês pelo GitHub Actions.
Grava um snapshot imutável por mês e gera um relatório do que mudou.

Princípios:
  1. Snapshot nacional. O recorte de estados se faz na análise, nunca na coleta.
  2. Arquivo nunca é sobrescrito. O nome carrega o mês da coleta.
  3. Falha em uma fonte não derruba as outras.
  4. O diff contra o mês anterior é o produto, não o snapshot isolado.
"""
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone, timedelta

import geopandas as gpd
import pandas as pd
import requests

import fontes

BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT)
REF = HOJE.strftime("%Y%m")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "dados")
RELAT = os.path.join(RAIZ, "relatorios")

CAMADAS = {
    "pocos": ["poco"],
    "campos": ["campo"],
    "blocos": ["bloco"],
}

log = []


def registrar(msg, ok=True):
    marca = "OK  " if ok else "ERRO"
    linha = f"[{marca}] {msg}"
    print(linha, flush=True)
    log.append(linha)


def pasta(*p):
    caminho = os.path.join(DADOS, *p)
    os.makedirs(caminho, exist_ok=True)
    return caminho


# ---------------------------------------------------------------- camadas WFS
def coletar_camadas():
    try:
        disponiveis = fontes.camadas_wfs()
        registrar(f"GeoServer respondeu com {len(disponiveis)} camadas")
    except Exception as e:
        registrar(f"GetCapabilities falhou: {e}", False)
        return

    for apelido, termos in CAMADAS.items():
        alvo = next((c for c in disponiveis
                     if all(t in c.lower() for t in termos)), None)
        if not alvo:
            registrar(f"{apelido}: nenhuma camada casou com {termos}", False)
            continue
        try:
            bruto = fontes.baixar(fontes.url_geojson(alvo), binario=True)
            g = gpd.read_file(io.BytesIO(bruto))
            if g.crs is None:
                g.set_crs(4674, inplace=True)
            g = g.to_crs(4674)
            g["_coletado_em"] = HOJE.date().isoformat()
            g["_camada_origem"] = alvo
            destino = os.path.join(pasta(apelido), f"{apelido}_{REF}.parquet")
            g.to_parquet(destino, compression="zstd", index=False)
            mb = os.path.getsize(destino) / 1e6
            registrar(f"{apelido}: {len(g):,} feicoes de {alvo} -> {mb:.1f} MB")
        except Exception as e:
            registrar(f"{apelido}: {e}", False)


# --------------------------------------------------------------- CSVs da ANP
ANO_RE = re.compile(r"(20\d{2})")


def ano_do_nome(url):
    """Extrai o ano do nome do arquivo. Retorna 0 se nao houver."""
    nome = url.rsplit("/", 1)[-1]
    achados = ANO_RE.findall(nome)
    return int(achados[-1]) if achados else 0


def mais_recentes(links, n=2):
    """Ordena por ano decrescente e devolve os n primeiros.

    A ANP publica com atraso: em setembro de 2026 o arquivo mais novo
    ainda era de 2025. Por isso nao se filtra pelo ano corrente — pega-se
    o que existe de mais recente.
    """
    com_ano = [l for l in links if ano_do_nome(l)]
    sem_ano = [l for l in links if not ano_do_nome(l)]
    com_ano.sort(key=ano_do_nome, reverse=True)
    return com_ano[:n] + sem_ano


def salvar_csv(url, sub, prefixo):
    nome = url.rsplit("/", 1)[-1]
    destino = os.path.join(pasta(sub), f"{prefixo}_{REF}__{nome}")
    if os.path.exists(destino):
        return None
    conteudo = fontes.baixar(url, binario=True)
    with open(destino, "wb") as f:
        f.write(conteudo)
    return destino


def catalogar(nome, links):
    """Grava todos os links vistos na pagina. Se a ANP mudar a estrutura,
    o catalogo mostra os nomes reais e o conserto vira ajuste de filtro."""
    os.makedirs(RELAT, exist_ok=True)
    caminho = os.path.join(RELAT, f"catalogo_{REF}.txt")
    with open(caminho, "a", encoding="utf-8") as f:
        f.write(f"\n===== {nome} — {len(links)} arquivos\n")
        for l in links:
            f.write(f"  {l.rsplit('/',1)[-1]}\n")


GRUPOS_PG = [
    ("royalties municipios", ("royalt", "municipi"), 2),
    ("royalties estados",    ("royalt", "estado"),   2),
    ("royalties uniao",      ("royalt", "uniao"),    2),
    ("participacao especial", ("pe_",),              9),
    ("preco referencia petroleo", ("preco", "petroleo"), 2),
    ("preco referencia gas",      ("preco", "gn"),       2),
]


def coletar_participacoes():
    try:
        links = fontes.links_da_pagina(fontes.PAGINAS["participacoes"])
    except Exception as e:
        registrar(f"participacoes: pagina inacessivel: {e}", False)
        return
    catalogar("participacoes-governamentais", links)

    baixados = 0
    for rotulo, termos, quantos in GRUPOS_PG:
        alvos = mais_recentes(fontes.filtrar(links, *termos), quantos)
        if not alvos:
            registrar(f"participacoes: nada encontrado para {rotulo}", False)
            continue
        for url in alvos:
            try:
                d = salvar_csv(url, "participacoes", "pg")
                nome = url.rsplit("/", 1)[-1]
                registrar(f"participacoes [{rotulo}]: {nome}"
                          + ("" if d else " (ja existia)"))
                baixados += 1
            except Exception as e:
                registrar(f"participacoes {url}: {e}", False)
    registrar(f"participacoes: {baixados} arquivos processados")


def coletar_producao():
    try:
        links = fontes.links_da_pagina(fontes.PAGINAS["producao_poco"])
    except Exception as e:
        registrar(f"producao: pagina inacessivel: {e}", False)
        return
    catalogar("producao-por-poco", links)
    if not links:
        registrar("producao: pagina sem arquivos — ver catalogo", False)
        return
    alvos = mais_recentes(links, 4)
    for url in alvos:
        try:
            d = salvar_csv(url, "producao", "prod")
            registrar(f"producao: {url.rsplit('/',1)[-1]}"
                      + ("" if d else " (ja existia)"))
        except Exception as e:
            registrar(f"producao {url}: {e}", False)


# ------------------------------------------------------------------- o diff
CHAVE = ["name", "poco", "nom_poco", "feicao_id"]
ACOMPANHAR = ["situacao", "categoria", "nome_campo", "operador",
              "reclassifi", "ambiente"]


def coluna(g, candidatas):
    for c in candidatas:
        if c in g.columns:
            return c
    return None


def gerar_diff():
    dir_p = os.path.join(DADOS, "pocos")
    if not os.path.isdir(dir_p):
        return
    arquivos = sorted(f for f in os.listdir(dir_p) if f.endswith(".parquet"))
    if len(arquivos) < 2:
        registrar("diff: primeiro snapshot, nada a comparar")
        return
    ant, atu = arquivos[-2], arquivos[-1]
    a = pd.read_parquet(os.path.join(dir_p, ant))
    b = pd.read_parquet(os.path.join(dir_p, atu))
    k = coluna(b, CHAVE)
    if not k:
        registrar("diff: chave de poco nao identificada", False)
        return

    sa, sb = set(a[k].dropna()), set(b[k].dropna())
    novos, sumidos = sb - sa, sa - sb

    linhas = [f"# Relatorio de mudancas — {REF}", "",
              f"Comparando `{ant}` com `{atu}`.", "",
              f"- Pocos no snapshot anterior: **{len(a):,}**",
              f"- Pocos no snapshot atual: **{len(b):,}**",
              f"- Entraram no cadastro: **{len(novos):,}**",
              f"- Sairam do cadastro: **{len(sumidos):,}**", ""]

    comuns = list(sa & sb)
    ia = a[a[k].isin(comuns)].drop_duplicates(k).set_index(k)
    ib = b[b[k].isin(comuns)].drop_duplicates(k).set_index(k)
    for campo in ACOMPANHAR:
        if campo not in ia.columns or campo not in ib.columns:
            continue
        mud = ia[campo].astype(str).ne(ib[campo].astype(str))
        n = int(mud.sum())
        if not n:
            continue
        linhas.append(f"## `{campo}` — {n} mudancas")
        linhas.append("")
        linhas.append("| Poco | Antes | Depois |")
        linhas.append("|---|---|---|")
        for idx in ia.index[mud][:40]:
            linhas.append(f"| {idx} | {ia.loc[idx, campo]} | {ib.loc[idx, campo]} |")
        if n > 40:
            linhas.append(f"| ... | mais {n-40} | |")
        linhas.append("")

    linhas += ["---", "",
               "> Mudanca de cadastro nao e o mesmo que mudanca no mundo. A ANP",
               "> tambem corrige registros retroativamente. Este relatorio mistura",
               "> os dois efeitos e nao permite separa-los com certeza."]

    os.makedirs(RELAT, exist_ok=True)
    destino = os.path.join(RELAT, f"mudancas_{REF}.md")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    registrar(f"diff: {len(novos)} novos, {len(sumidos)} removidos -> {destino}")


# ---------------------------------------------------------------------- main
def main():
    registrar(f"Coleta de referencia {REF} iniciada em {HOJE.isoformat()}")
    coletar_camadas()
    coletar_participacoes()
    coletar_producao()
    gerar_diff()

    os.makedirs(RELAT, exist_ok=True)
    with open(os.path.join(RELAT, f"log_{REF}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log))

    erros = [l for l in log if l.startswith("[ERRO")]
    if erros:
        print(f"\n{len(erros)} fonte(s) falharam — snapshot parcial gravado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
