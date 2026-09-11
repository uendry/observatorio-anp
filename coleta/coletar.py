name: Coleta mensal ANP

on:
  schedule:
    # dia 5 de cada mes, 09:00 UTC = 06:00 em Brasilia
    - cron: "0 9 5 * *"
  workflow_dispatch:        # permite rodar na mao pela aba Actions

permissions:
  contents: write           # necessario para o commit do snapshot

jobs:
  coletar:
    runs-on: ubuntu-latest
    timeout-minutes: 45

    steps:
      - name: Clonar o repositorio
        uses: actions/checkout@v4

      - name: Preparar Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Instalar dependencias
        run: pip install -r coleta/requirements.txt

      - name: Executar a coleta
        working-directory: coleta
        run: python coletar.py

      - name: Commitar o snapshot
        run: |
          git config user.name  "coleta-automatica"
          git config user.email "actions@github.com"
          git add dados relatorios
          if git diff --staged --quiet; then
            echo "Nada mudou neste mes."
          else
            git commit -m "Coleta ANP $(date -u +%Y-%m)"
            git push
          fi
