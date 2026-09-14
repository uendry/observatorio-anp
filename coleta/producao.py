"""
producao.py — coleta da produção por poço, com verificação de período.

Por que este módulo existe
--------------------------
A coleta 202609 gravou dezesseis arquivos de produção rotulados como atuais.
Todos eram de 2022 e 2023. A falha teve duas causas independentes:

1. `mais_recentes()` devolvia `com_ano[:n] + sem_ano`. Os arquivos sem ano no
   nome entravam todos, sem corte. E `producao-01.zip` … `producao-12.zip` não
   têm ano no nome.

2. A causa de fundo, que nenhum ajuste de ordenação resolve: a seção
   "Produção por Poço 2024 em diante" da página de Dados Abertos aponta seus
   doze links mensais para o diretório `/2023/`. A ANP trocou o título da seção
   e manteve os links antigos. A página não publica ZIP de 2024 em diante.

A lição estrutural: nome de arquivo e rótulo de seção são afirmações do
publicador, não fatos. O período só é fato quando lido de dentro do dado.
Este módulo nunca confia no nome.

Limite conhecido
----------------
De 2024 em diante a produção por poço só existe no CDP
(cdp.anp.gov.br/ords/r/cdp_apex/…/consulta-produção-por-poço), que exige
captcha a cada consulta. Não há rota automatizável. Este módulo coleta o que a
página pública oferece, marca a defasagem e falha ruidosamente quando o dado
mais novo está mais velho que o esperado. A janela recente entra por ingestão
manual — ver `ingerir_manual()`.
"""
import io
import os
import re
import zipfile
from datetime import datetime, timezone, timedelta

import pandas as pd

BRT = timezone(timedelta(hours=-3))
DEFASAGEM_MESES = 2          # regra da ANP: produção de janeiro sai em março
PERIODO_RE = re.compile(r"(20\d{2})[/_-](0[1-9]|1[0-2])")


# --------------------------------------------------------------- período real
def _ler_csv(raw):
    """A ANP alterna entre UTF-8 com BOM e latin-1 no mesmo conjunto."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            d = pd.read_csv(io.BytesIO(raw), sep=";", encoding=enc,
                            dtype=str, nrows=5000, low_memory=False)
            if d.shape[1] > 1:
                return d
        except Exception:
            continue
    return None


def periodo_do_conteudo(raw_zip):
    """Período (AAAAMM) lido de dentro do ZIP. None se não for determinável.

    Lê a coluna de período do CSV, não o nome do arquivo. Se a coluna sumir,
    cai para o nome das entradas internas do ZIP, que a ANP nomeia
    `AAAA_MM_producao_Mar.csv`. Só então desiste.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile:
        return None

    for nome in z.namelist():
        if not nome.lower().endswith(".csv"):
            continue
        d = _ler_csv(z.read(nome))
        if d is None:
            continue
        cols = [c for c in d.columns
                if "per" in c.lower() and "carga" not in c.lower()]
        if cols:
            vals = d[cols[0]].dropna().astype(str)
            achados = {m.group(0).replace("/", "").replace("_", "").replace("-", "")
                       for v in vals for m in [PERIODO_RE.search(v)] if m}
            if achados:
                return max(achados)

    for nome in z.namelist():                      # fallback pelo nome interno
        m = PERIODO_RE.search(nome)
        if m:
            return m.group(1) + m.group(2)
    return None


def periodo_esperado(hoje=None):
    """Período mais recente que a ANP deveria ter publicado hoje."""
    hoje = hoje or datetime.now(BRT)
    ano, mes = hoje.year, hoje.month - DEFASAGEM_MESES
    while mes < 1:
        mes += 12
        ano -= 1
    return f"{ano}{mes:02d}"


def meses_de_atraso(periodo, hoje=None):
    alvo = periodo_esperado(hoje)
    a1, m1 = int(periodo[:4]), int(periodo[4:])
    a2, m2 = int(alvo[:4]), int(alvo[4:])
    return (a2 - a1) * 12 + (m2 - m1)


# --------------------------------------------------------------- ordenação
def mais_recentes(links, n=2, ano_do_nome=None):
    """Corrige `com_ano[:n] + sem_ano`.

    Antes, todo link sem ano no nome passava sem corte — foi por aí que os doze
    ZIPs de 2023 entraram. Agora os sem ano são candidatos de última ordem e
    também respeitam o limite n.
    """
    com_ano = sorted([l for l in links if ano_do_nome(l)],
                     key=ano_do_nome, reverse=True)
    sem_ano = [l for l in links if not ano_do_nome(l)]
    return (com_ano + sem_ano)[:n]


# --------------------------------------------------------------- coleta
def coletar(links, baixar, pasta_dados, ref, registrar, candidatos=6,
            ano_do_nome=None, hoje=None):
    """Baixa candidatos, nomeia pelo período REAL e avalia o frescor.

    Grava `prod_<ref>__<periodo_real>.zip`. Um arquivo cujo nome diz uma coisa
    e cujo conteúdo diz outra passa a ser impossível.
    """
    alvos = mais_recentes(links, candidatos, ano_do_nome)
    destino = os.path.join(pasta_dados, "producao")
    os.makedirs(destino, exist_ok=True)

    periodos = []
    for url in alvos:
        nome_origem = url.rsplit("/", 1)[-1]
        try:
            raw = baixar(url, binario=True)
        except Exception as e:
            registrar(f"producao {nome_origem}: {e}", False)
            continue

        periodo = periodo_do_conteudo(raw)
        if periodo is None:
            registrar(f"producao {nome_origem}: periodo nao determinavel — descartado", False)
            continue

        atraso = meses_de_atraso(periodo, hoje)
        caminho = os.path.join(destino, f"prod_{ref}__{periodo}.zip")
        if os.path.exists(caminho):
            registrar(f"producao {periodo}: ja existia")
            periodos.append(periodo)
            continue

        with open(caminho, "wb") as f:
            f.write(raw)
        periodos.append(periodo)
        aviso = "" if atraso <= 0 else f" — {atraso} meses atras do esperado"
        registrar(f"producao {periodo}: de {nome_origem}{aviso}", atraso <= 1)

    if not periodos:
        registrar("producao: nenhum arquivo valido coletado", False)
        return None

    novo = max(periodos)
    atraso = meses_de_atraso(novo, hoje)
    esperado = periodo_esperado(hoje)
    if atraso <= 1:
        registrar(f"producao: periodo mais novo {novo}, dentro do esperado ({esperado})")
    else:
        registrar(
            f"producao: DEFASADO — mais novo e {novo}, esperado {esperado} "
            f"({atraso} meses). A pagina de Dados Abertos nao publica ZIP de "
            f"2024 em diante; a janela recente so existe no CDP, com captcha. "
            f"Usar ingerir_manual() ate que haja rota automatizavel.", False)
    return novo


# --------------------------------------------------------------- manual
def ingerir_manual(caminho_zip, pasta_dados, ref, registrar):
    """Registra um ZIP exportado à mão do CDP, sob a mesma regra de nomenclatura.

    O arquivo entra no acervo com o período lido do conteúdo, igual ao que vem
    da coleta automática. Origem manual não afeta o nome, só o log.
    """
    with open(caminho_zip, "rb") as f:
        raw = f.read()
    periodo = periodo_do_conteudo(raw)
    if periodo is None:
        registrar(f"producao manual {os.path.basename(caminho_zip)}: "
                  f"periodo nao determinavel — recusado", False)
        return None
    destino = os.path.join(pasta_dados, "producao")
    os.makedirs(destino, exist_ok=True)
    alvo = os.path.join(destino, f"prod_{ref}__{periodo}.zip")
    if os.path.exists(alvo):
        registrar(f"producao manual {periodo}: ja existia")
        return periodo
    with open(alvo, "wb") as f:
        f.write(raw)
    registrar(f"producao manual {periodo}: ingerido de {os.path.basename(caminho_zip)}")
    return periodo
