"""
fontes.py — descoberta das fontes da ANP em tempo de execução.

Por que descobrir em vez de fixar a URL: a ANP renomeia arquivos sem aviso.
Já vimos 'royalties-municipio-2025.csv' e 'royalties-municipios-2023.csv'
convivendo na mesma pasta. URL fixa quebra; descoberta sobrevive.
"""
import re
import requests

UA = {"User-Agent": "CartoGeo-Observatorio/1.0 (coleta mensal de dados publicos ANP)"}
TIMEOUT = 120

WFS = "https://gishub.anp.gov.br/geoserver/BD_ANP/ows"

PAGINAS = {
    "participacoes": "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/participacoes-governamentais",
    "producao_poco": "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-poco",
}


def baixar(url, binario=False):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content if binario else r.text


def links_da_pagina(url, extensoes=(".csv", ".zip", ".xlsx")):
    """Extrai links absolutos de arquivos de uma página da ANP."""
    html = baixar(url)
    achados = set()
    for m in re.finditer(r'href="([^"]+)"', html):
        href = m.group(1)
        if not href.lower().endswith(extensoes):
            continue
        if href.startswith("/"):
            href = "https://www.gov.br" + href
        if href.startswith("http"):
            achados.add(href)
    return sorted(achados)


def filtrar(links, *termos):
    """Mantém links cujo nome contenha todos os termos (sem acento, minúsculo)."""
    out = []
    for l in links:
        nome = l.rsplit("/", 1)[-1].lower()
        if all(t in nome for t in termos):
            out.append(l)
    return out


def camadas_wfs():
    """Lista as camadas publicadas no GeoServer da ANP."""
    url = f"{WFS}?service=WFS&version=1.1.0&request=GetCapabilities"
    xml = baixar(url)
    return sorted(set(re.findall(r"<Name>(BD_ANP:[^<]+)</Name>", xml)))


def url_geojson(camada, max_features=None):
    u = (f"{WFS}?service=WFS&version=1.0.0&request=GetFeature"
         f"&typeName={camada}&outputFormat=application/json")
    if max_features:
        u += f"&maxFeatures={max_features}"
    return u
