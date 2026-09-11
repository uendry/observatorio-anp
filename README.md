Observatório ANP — CartoGeo
Coleta mensal automatizada das bases públicas da ANP, com histórico versionado.
Por que este repositório existe
A camada de poços da ANP não é uma série histórica — é um retrato do estado atual.
O GeoServer responde com o banco no instante da consulta. Não existe campo de
data-referência que permita perguntar "como estava em março".
Consequência: a periodicidade mensal não existe no dado. Ela é criada aqui,
por disciplina de coleta. O produto não é o retrato de um mês — é a diferença
entre um mês e o anterior, que ninguém mais tem porque ninguém guardou.
Protocolo
Regra	Valor
Dia da coleta	5 de cada mês, 06:00 (Brasília)
Escopo	Brasil inteiro. O recorte de estados se faz na análise
Nomenclatura	`<base>_AAAAMM.parquet`
Sobrescrita	Nunca. Arquivo gravado é imutável
Formato	GeoParquet com compressão zstd
O escopo nacional não é excesso de zelo: a troca de Pernambuco por Alagoas no
recorte comercial custou reprocessamento. Snapshot nacional torna mudança de
escopo um filtro, não um retrabalho.
Estrutura
```
.github/workflows/coleta.yml   agendamento e execução
coleta/
  fontes.py                    descoberta das URLs da ANP
  coletar.py                   coleta, gravação e diff
  requirements.txt
dados/
  pocos/pocos_AAAAMM.parquet
  campos/campos_AAAAMM.parquet
  blocos/blocos_AAAAMM.parquet
  participacoes/pg_AAAAMM__<arquivo original>.csv
  producao/prod_AAAAMM__<arquivo original>.csv
relatorios/
  mudancas_AAAAMM.md           o que mudou desde o mês anterior
  log_AAAAMM.txt               o que funcionou e o que falhou
```
Fontes
Base	Origem	Cadência real
Poços, campos, blocos	GeoServer da ANP (WFS)	Estado atual, contínuo
Royalties por município	Dados Abertos — Participações Governamentais	Mensal
Participação especial	Dados Abertos — Participações Governamentais	Mensal
Produção por poço	Dados Abertos — Produção por Poço	Mensal, ~2 meses de defasagem
As URLs não são fixas no código. A ANP renomeia arquivos sem aviso — já
convivem `royalties-municipio-2025.csv` e `royalties-municipios-2023.csv` na
mesma pasta. O coletor lê a página de índice e descobre os links a cada execução.
Poço tem volume. Campo tem dinheiro.
O shapefile de poços não traz valor nem produção. A cadeia é:
Nível	O que existe
Poço	Volume produzido, mensal
Campo	Volume agregado + preço de referência
Participações	Royalties = produção × preço de referência × alíquota
Município	Repasse, por confrontação
O royalty é calculado por campo, não por poço. Valor por poço é número
derivado, não oficial — serve para priorizar internamente, nunca para peça
técnica sem estar rotulado como estimativa.
Competência ou caixa
O arquivo de royalties traz duas datas. Escolher a errada desalinha a série
em cerca de dois meses:
competência — mês da produção. Use para análise técnica ligando produção a dinheiro
caixa — mês do repasse. Use para argumento financeiro: é quando o dinheiro entrou na conta
Limitações conhecidas
O diff mistura dois efeitos. A ANP corrige registros retroativamente.
Não há como separar com certeza "o mundo mudou" de "o cadastro foi corrigido".
Todo relatório carrega essa ressalva.
A página de Dados Abertos publica arquivos desatualizados. Em agosto de
2026, os shapefiles estáticos eram de 2023, enquanto o GeoServer estava em dia.
Por isso a coleta usa o WFS, não os `.zip` da página.
`maxFeatures` trunca em silêncio. O coletor não usa esse parâmetro.
Falha parcial é tolerada. Uma fonte fora do ar não derruba as demais;
o log registra o que faltou.
Execução manual
Aba Actions → Coleta mensal ANP → Run workflow.
Licença dos dados
Dados da ANP, públicos, Creative Commons Attribution. Este repositório guarda
cópias datadas; a fonte primária é sempre a ANP.
