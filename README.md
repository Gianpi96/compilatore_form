# DocForm

Estrattore documenti d'identità con verifica umana e compilazione form. Vedi [SPEC.md](SPEC.md) per i dettagli completi e [TASKS.md](TASKS.md) per lo stato di avanzamento.

## Avvio su Windows (sviluppo locale)

Requisiti: Python 3.14 installato (`py -0p` per verificare le versioni disponibili).

```powershell
# 1. Crea e attiva un ambiente virtuale
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Installa le dipendenze
pip install -r requirements.txt

# 3. Copia il file di configurazione e compilalo
copy .env.example .env
# poi apri .env e imposta GROQ_API_KEY, OUTPUT_FOLDER, SECRET_KEY

# 4. Avvia il server di sviluppo
uvicorn app.main:app --reload
```

L'app risponde su `http://localhost:8000`. Verifica che sia attiva con:

```powershell
curl http://localhost:8000/health
```

**Nota:** ogni comando (`uvicorn`, `pytest`, `ruff`, ecc.) va eseguito con il venv attivato (prompt con `(.venv)` davanti), altrimenti Windows potrebbe risolvere il comando su un'installazione Python diversa. Se `.venv\Scripts\Activate.ps1` fallisce con un errore di "execution policy", eseguire prima `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. In alternativa, senza attivare il venv, si può chiamare l'eseguibile direttamente: `.venv\Scripts\uvicorn.exe app.main:app --reload`.

Il comando resta in esecuzione (è un server): `Ctrl+C` per fermarlo va premuto solo dopo aver visto la riga `Application startup complete`, non prima.

## Sviluppo

```powershell
# Lint e formattazione
ruff check .

# Test
pytest

# Scan dipendenze (vulnerabilità note)
pip-audit

# Scan statico di sicurezza
bandit -r app -c pyproject.toml
```

## Struttura del progetto

```
app/            codice applicativo (FastAPI)
tests/          test pytest
migrations/     migrazioni Alembic (schema DB, vedi TASK 2)
requirements.txt   dipendenze Python
pyproject.toml      configurazione ruff/pytest
.env.example        variabili d'ambiente richieste (copiare in .env)
```

## Dati e privacy

Le immagini dei documenti caricati sono temporanee: vengono cancellate fisicamente alla conferma della pratica (dopo la generazione del PDF) o dopo 24h di abbandono. La cartella `OUTPUT_FOLDER` (PDF confermati) e il file `docform.db` vanno inclusi nel backup dell'ufficio — sono il dato definitivo del sistema. Vedi SPEC.md §2 per i dettagli.
