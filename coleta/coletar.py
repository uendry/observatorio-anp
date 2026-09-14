"""
coletar.py — coleta mensal das bases públicas da ANP.

Roda no dia 5 de cada mês pelo GitHub Actions.
Grava um snapshot imutável por mês e gera um relatório do que mudou.

Princípios:
  1. Snapshot nacional. O recorte de estados se faz na análise, nunca na coleta.
  2. Arquivo nunca é sobrescrito. O nome carrega o mês da coleta.
  3. Falha em uma fonte não derruba as outras.
  4. O diff contra o mês anterior é o produto, não o snapshot isolado.
  5. Período é fato lido do conteúdo, nunca afirmação do nome do arquivo.
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
import producao

BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT)
REF = HOJE.strftime("%Y%m")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "dados")
RELAT = os.path.join(RAIZ, "relatorios")

REPO = "uendry/observatorio-anp"
RAMO = "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{RAMO}/"

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
    """Delegado para producao.mais_recentes.

    A versao anterior devolvia `com_ano[:n] + sem_ano`: todo link sem ano no
    nome passava sem corte. Foi assim que doze ZIPs de 2023 entraram na coleta
    202609. O mesmo defeito afeta royalties e participacao especial, onde
    passava despercebido so porque aqueles arquivos trazem o ano no nome.
    """
    return producao.mais_recentes(links, n, ano_do_nome)


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
    """Coleta a producao por poco com verificacao de periodo.

    A pagina de Dados Abertos nao publica ZIP de 2024 em diante: a secao
    "Producao por Poco 2024 em diante" aponta seus doze links mensais para o
    diretorio /2023/. De 2024 em diante o dado so existe no CDP, que exige
    captcha. Esta funcao coleta o que a pagina oferece, nomeia pelo periodo
    real e denuncia a defasagem no log e no indice.
    """
    try:
        links = fontes.links_da_pagina(fontes.PAGINAS["producao_poco"])
    except Exception as e:
        registrar(f"producao: pagina inacessivel: {e}", False)
        return None
    catalogar("producao-por-poco", links)
    if not links:
        registrar("producao: pagina sem arquivos — ver catalogo", False)
        return None
    return producao.coletar(links, fontes.baixar, DADOS, REF, registrar,
                            candidatos=6, ano_do_nome=ano_do_nome, hoje=HOJE)


# ------------------------------------------------------- consolidacao analitica
COLUNAS_PROD = {
    "estado": "estado",
    "bacia": "bacia",
    "nome poco anp": "poco_anp",
    "nome poco operador": "poco_operador",
    "campo": "campo",
    "operador": "operador",
    "numero do contrato": "contrato",
    "periodo": "periodo_bruto",
    "oleo (bbl/dia)": "oleo_bbl_dia",
    "condensado (bbl/dia)": "condensado_bbl_dia",
    "petroleo (bbl/dia)": "petroleo_bbl_dia",
    "gas natural (mm3/dia) total": "gas_mm3_dia",
    "volume gas royalties (m3/mes)": "gas_royalties_m3_mes",
    "agua (bbl/dia)": "agua_bbl_dia",
    "instalacao destino": "instalacao_destino",
    "tempo de producao (hs por mes)": "horas_producao_mes",
    "grau api": "grau_api",
}

ACENTOS = str.maketrans("áàâãäéêëíïóôõöúüçÁÀÂÃÄÉÊËÍÏÓÔÕÖÚÜÇ",
                        "aaaaaeeeiioooouucAAAAAEEEIIOOOOUUC")


def _norm(c):
    return " ".join(c.translate(ACENTOS).lower().replace("³", "3").split())


def _num_br(s):
    """Converte o formato numerico brasileiro da ANP: 1.234,56 -> 1234.56."""
    return pd.to_numeric(
        s.astype(str).str.strip()
         .str.replace(".", "", regex=False)
         .str.replace(",", ".", regex=False)
         .replace({"": None, "nan": None}),
        errors="coerce")


def consolidar_producao():
    """Le todos os ZIPs de producao e grava um parquet normalizado.

    Sem isso, quem for analisar precisa reabrir dezesseis ZIPs, adivinhar entre
    utf-8-sig e latin-1 e converter virgula decimal a cada vez. O parquet
    resolve isso uma vez por coleta. Nao substitui os ZIPs: eles seguem sendo
    o dado bruto imutavel.
    """
    dir_p = os.path.join(DADOS, "producao")
    if not os.path.isdir(dir_p):
        return None
    zips = sorted(f for f in os.listdir(dir_p) if f.endswith(".zip"))
    if not zips:
        return None

    partes = []
    for nome in zips:
        try:
            z = zipfile.ZipFile(os.path.join(dir_p, nome))
        except zipfile.BadZipFile:
            registrar(f"consolidacao: {nome} nao e um zip valido", False)
            continue
        for interno in z.namelist():
            if not interno.lower().endswith(".csv"):
                continue
            amb = ("MAR" if "mar" in interno.lower() else
                   "PRESAL" if "presal" in interno.lower() else
                   "TERRA" if "terra" in interno.lower() else "?")
            raw = z.read(interno)
            d = None
            for enc in ("utf-8-sig", "latin-1"):
                try:
                    d = pd.read_csv(io.BytesIO(raw), sep=";", encoding=enc,
                                    dtype=str, low_memory=False)
                    if d.shape[1] > 1:
                        break
                except Exception:
                    d = None
            if d is None or d.shape[1] < 2:
                continue
            d.columns = [_norm(c) for c in d.columns]
            manter = {o: n for o, n in COLUNAS_PROD.items() if o in d.columns}
            d = d[list(manter)].rename(columns=manter)
            d["ambiente"] = amb
            d["_arquivo"] = nome
            partes.append(d)

    if not partes:
        registrar("consolidacao: nenhum CSV de producao legivel", False)
        return None

    p = pd.concat(partes, ignore_index=True)
    if "periodo_bruto" in p.columns:
        ext = p["periodo_bruto"].astype(str).str.extract(r"(20\d{2})[/_-](\d{2})")
        p["periodo"] = ext[0] + ext[1]
    for c in [c for c in p.columns if c.endswith(("_dia", "_mes", "_api"))]:
        p[c] = _num_br(p[c])

    destino = os.path.join(pasta("derivados"), f"producao_poco_{REF}.parquet")
    p.to_parquet(destino, compression="zstd", index=False)
    mb = os.path.getsize(destino) / 1e6
    faixa = f"{p.periodo.min()}–{p.periodo.max()}" if "periodo" in p else "?"
    registrar(f"consolidacao: {len(p):,} linhas, periodos {faixa} -> {mb:.1f} MB")
    return destino


# ------------------------------------------------------------------ indice
def _listar(sub):
    d = os.path.join(DADOS, sub)
    if not os.path.isdir(d):
        return []
    return [{"arquivo": f,
             "caminho": f"dados/{sub}/{f}",
             "url": RAW + f"dados/{sub}/{f}",
             "bytes": os.path.getsize(os.path.join(d, f))}
            for f in sorted(os.listdir(d))]


def escrever_indice(periodo_prod):
    """Grava indice.json na raiz: o que existe, de quando, e onde buscar.

    Serve para que uma analise externa descubra o acervo com uma requisicao
    unica ao raw.githubusercontent, em vez de clonar o repositorio inteiro.
    Tambem carrega o alarme de defasagem em campo estruturado, nao so em log.
    """
    esperado = producao.periodo_esperado(HOJE)
    atraso = producao.meses_de_atraso(periodo_prod, HOJE) if periodo_prod else None

    avisos = []
    if atraso is not None and atraso > 1:
        avisos.append(
            f"Producao por poco defasada em {atraso} meses: mais novo {periodo_prod}, "
            f"esperado {esperado}. A pagina de Dados Abertos da ANP nao publica ZIP "
            f"de 2024 em diante; a janela recente so existe no CDP, com captcha.")

    indice = {
        "gerado_em": HOJE.isoformat(),
        "referencia": REF,
        "repo": REPO,
        "raw_base": RAW,
        "producao": {
            "periodo_mais_novo": periodo_prod,
            "periodo_esperado": esperado,
            "meses_de_atraso": atraso,
            "arquivos": _listar("producao"),
        },
        "camadas": {k: _listar(k) for k in CAMADAS},
        "participacoes": _listar("participacoes"),
        "derivados": _listar("derivados"),
        "relatorios": [
            {"arquivo": f, "caminho": f"relatorios/{f}",
             "url": RAW + f"relatorios/{f}"}
            for f in sorted(os.listdir(RELAT))] if os.path.isdir(RELAT) else [],
        "avisos": avisos,
        "erros_da_coleta": [l for l in log if l.startswith("[ERRO")],
    }
    destino = os.path.join(RAIZ, "indice.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(indice, f, ensure_ascii=False, indent=2)
    registrar(f"indice: {destino}")
    return destino


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
    periodo_prod = coletar_producao()
    consolidar_producao()
    gerar_diff()

    os.makedirs(RELAT, exist_ok=True)
    with open(os.path.join(RELAT, f"log_{REF}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log))

    escrever_indice(periodo_prod)

    erros = [l for l in log if l.startswith("[ERRO")]
    if erros:
        print(f"\n{len(erros)} fonte(s) falharam — snapshot parcial gravado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
