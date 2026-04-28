# Google Maps Brazil Scraper


PRA USAR VC BASICAMENTE TEM Q INSTALAR OS REQUIREMENTS, TO USANDO O PYTHON 3.12 se n ir ai e isso

AI VC FAZ O python scrape.py --query "intelbras" --instances X (NUMERO DE BROWSERS ABERTOS) --headless (se vc n quiser ver os browsers abertos vc bota esse headless ai)


Uso basico:

```powershell
uv run python scrape.py --query "camarao"
```

Instalacao:

```powershell
uv venv
uv pip install -r requirements.txt
uv run playwright install chromium
```

Rodar sem abrir navegador visivel:

```powershell
uv run python scrape.py --query "camarao" --headless
```

Rodar sem dashboard:

```powershell
uv run python scrape.py --query "camarao" --headless --no-dashboard
```

Rodar com mais de uma instancia do Google Maps:

```powershell
uv run python scrape.py --query "camarao" --instances 2
```

Recomecar a mesma busca do zero:

```powershell
uv run python scrape.py --query "camarao" --reset
```

Continuar uma busca interrompida:

```powershell
uv run python scrape.py --query "camarao" --resume
```

Testar em uma area menor (ESSA PORRA DA FALHANDO MAS VSF):

```powershell
uv run python scrape.py --query "camarao" --bbox="-23.70,-46.80,-23.45,-46.50" --start-cell-km=30 --headless --no-dashboard
```

Mudar a porta do dashboard:

```powershell
uv run python scrape.py --query "camarao" --port 5001
```

Arquivos gerados:

- `data/state.db`: estado da raspagem.
- `data/results_<query>.xlsx`: planilha com os resultados.
- `data/chrome-profile*/`: perfis do Chromium usados pelo Playwright.

Flags principais:

- `--query`: termo de busca.
- `--instances`: quantidade de navegadores em paralelo.
- `--reset`: apaga os dados daquela busca e comeca de novo.
- `--resume`: retoma uma busca parada.
- `--bbox`: limita a area no formato `min_lat,min_lon,max_lat,max_lon`.
- `--start-cell-km`: tamanho inicial das celulas.
- `--min-cell-km`: menor tamanho de celula permitido.
- `--headless`: roda o navegador sem janela.
- `--no-dashboard`: nao sobe o dashboard local.
- `--port`: porta do dashboard.

