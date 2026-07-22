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

## Database e migrazioni

Schema gestito con Alembic (mai modifiche manuali allo schema). Dopo aver installato le dipendenze:

```powershell
# Applica tutte le migrazioni (crea docform.db se non esiste)
alembic upgrade head

# Annulla l'ultima migrazione applicata
alembic downgrade -1

# (solo sviluppo) crea un utente di test — MAI da eseguire in produzione
python -m app.seed_dev
```

Il percorso del database è `sqlite:///./docform.db` di default; sovrascrivibile con la variabile d'ambiente `DATABASE_URL` (usata anche dai test, che comunque girano su un DB SQLite in memoria e non toccano mai `docform.db`).

## Autenticazione

v1 ha un solo utente (l'operatore), creato manualmente — non c'è auto-registrazione né recupero password via email (vedi SPEC.md §3).

- **Login:** `http://localhost:8000/login` (email + password).
- **Sessione:** cookie firmato (`itsdangerous`), `HttpOnly` + `SameSite=Strict`, valido 8 ore.
- **Password persa:** unico modo per recuperarla è resettarla da riga di comando, sulla macchina che ospita `docform.db`:

  ```powershell
  python -m app.cli reset-password operatore@esempio.it "nuova-password-almeno-12-caratteri"
  ```

  La password deve avere almeno 12 caratteri (nessun altro vincolo di composizione).
- **Brute-force:** `/login` è limitato a 5 tentativi ogni 5 minuti per IP; oltre la soglia risponde `429`.

## Nuova pratica (upload documento)

`http://localhost:8000/pratiche/nuova` — accetta JPEG, PNG o PDF (max 20 MB, PDF fino a 50 pagine).

- Il formato è verificato sui **magic bytes** reali del file, mai sull'estensione dichiarata dal client.
- Per un PDF multipagina, l'operatore sceglie quale pagina elaborare da un'anteprima; solo la pagina scelta viene renderizzata e salvata come immagine.
- L'hash SHA-256 del file caricato viene confrontato con le pratiche esistenti: se coincide, la nuova pratica viene comunque creata ma segnalata come possibile duplicato (avviso non bloccante, con link alla pratica originale).
- Le immagini caricate sono salvate in `storage/temp/` (non tracciata da git, cancellata alla conferma della pratica, dopo 24h di abbandono, o su annullamento manuale — vedi TASK 6/7/8).

Finché la pratica non è confermata (stato `caricato`, `in_verifica` o `estrazione_fallita`), è disponibile il pulsante **"Annulla pratica"** (richiede conferma): cancella fisicamente l'immagine e ripulisce i campi anagrafici estratti, portando la pratica a stato `annullato` — non elimina il record (resta per audit dei volumi).

## Estrazione automatica (Groq vision)

Subito dopo l'upload (nella stessa richiesta, senza coda), l'immagine viene inviata al modello vision configurato in `GROQ_MODEL` (default `qwen/qwen3.6-27b` in `app/groq_estrattore.py`, unico modello vision offerto da Groq alla data del 2026-07-21 e testato con una chiamata reale — **verificare comunque su [console.groq.com/docs/vision](https://console.groq.com/docs/vision)** se il catalogo modelli è cambiato). L'esito:

- **Successo:** campi (nome, cognome, data di nascita, luogo di nascita, codice fiscale, numero documento, data di scadenza) e relative confidenze salvati sulla pratica, stato `in_verifica`. Il codice fiscale viene anche validato (carattere di controllo + coerenza con nome/cognome/data di nascita, vedi `app/codice_fiscale.py`) e la scadenza confrontata con la data odierna (timezone Europe/Rome).
- **Errore/timeout/risposta non interpretabile:** stato `estrazione_fallita`, nessuna eccezione propagata (l'errore viene loggato con `logging`, senza dati anagrafici); i campi andranno inseriti manualmente (TASK 6).

Se un modello smette di funzionare (es. `model_not_found`), il messaggio d'errore compare nei log del server — non c'è bisogno di indovinare, basta leggerlo.

Il motore di estrazione è dietro un'interfaccia astratta (`app/estrattore.py`, `EstrattoreDocumento`) sostituibile senza toccare i chiamanti — utile se in futuro serve un motore locale che non invii mai i documenti fuori dal server (vedi SPEC.md §1).

## Verifica pratica

Dopo l'estrazione, `http://localhost:8000/pratiche/{id}` reindirizza automaticamente a `/pratiche/{id}/verifica`:

- **Estrazione riuscita** (`in_verifica`): immagine a fianco del form precompilato. I campi con confidenza sotto l'80% sono evidenziati e richiedono una conferma esplicita (HTMX, senza reload) prima che il pulsante "Conferma pratica" sia accettato dal server — un tentativo di conferma con campi ancora da confermare viene rifiutato con l'elenco esplicito dei campi mancanti.
- **Estrazione fallita** (`estrazione_fallita`): stessa schermata, ma tutti i campi sono vuoti e obbligatori (nessuna evidenziazione, non c'è confidenza da mostrare). Dopo il salvataggio la pratica torna in `in_verifica` (i campi inseriti a mano contano come già confermati) — serve comunque un ultimo click su "Conferma pratica".
- Avvisi non bloccanti in testa alla pagina: documento scaduto, codice fiscale incoerente, possibile duplicato.
- Il pulsante **"Annulla pratica"** resta disponibile anche da questa schermata.

Alla conferma la pratica passa a stato `confermato`: viene generato il PDF e cancellata fisicamente l'immagine (vedi sotto).

## Conferma e PDF

Al click su "Conferma pratica" (solo se tutti i campi a bassa confidenza sono stati confermati):

1. genera un PDF (`reportlab`) in `OUTPUT_FOLDER` con nome `AAAA-MM-GG_COGNOME-NOME.pdf` (data odierna, timezone Europe/Rome); se il nome esiste già, aggiunge un suffisso incrementale (`_2`, `_3`, ...) — mai una sovrascrittura. Scrittura atomica (file temporaneo + rename): mai un PDF parziale con il nome finale.
2. imposta `percorso_pdf_output` e `confermato_il`, transiziona la pratica a `confermato`.
3. cancella fisicamente l'immagine originale — solo dopo aver verificato che nessun'altra pratica referenzi lo stesso file e che la cancellazione sia effettivamente riuscita; un problema qui viene loggato (mai un'eccezione silenziosa) ma non annulla la conferma già avvenuta, dato che il PDF (il dato definitivo) è già stato scritto con successo.

Se la generazione del PDF fallisce (es. cartella non scrivibile), la conferma viene rifiutata e la pratica resta in `in_verifica`, pronta per un nuovo tentativo — l'errore va nei log del server, mai in uno stacktrace mostrato all'operatore.

Non c'è ancora un link di download diretto dalla UI (arriva con TASK 9, la dashboard): per ora il percorso del file è visibile nel dettaglio della pratica confermata.

## Job di pulizia pratiche abbandonate

All'avvio dell'app (log strutturati su stdout, vedi sotto) parte uno scheduler in-process (`APScheduler`) che:

1. esegue subito una passata di pulizia (così un'immagine non resta orfana per giorni se il PC è rimasto spento oltre le 24h);
2. poi ripete la stessa passata ogni ora, finché il processo resta acceso.

Ogni passata cerca le pratiche con `creato_il` più vecchio di 24 ore e stato non terminale (esclusi `confermato`, `abbandonato`, `annullato`): per ciascuna cancella fisicamente l'immagine, ripulisce (`NULL`) tutti i campi anagrafici e le confidenze, e porta lo stato ad `abbandonato` — stessa logica di minimizzazione dell'annullamento manuale, ma automatica e senza operatore associato nel log (`utente_id` nullo). Ogni pratica viene salvata singolarmente: un problema su una non fa perdere il lavoro già fatto sulle altre nella stessa passata.

**Nota tecnica:** l'app non configurava esplicitamente il modulo `logging` di Python — i messaggi `INFO` (incluso il riepilogo del job: "N pratica/e abbandonata/e") venivano scartati silenziosamente perché sotto la soglia di default. Corretto in `app/main.py` con una configurazione esplicita (livello INFO, formato con timestamp) applicata all'avvio: da questo momento tutti i log applicativi (incluse le estrazioni fallite già presenti dal TASK 5) sono visibili nell'output del server.

**Collaudo manuale senza attendere 24h:**

```powershell
# 1. Trova (o crea) l'id di una pratica di test, poi:
python -m app.cli forza-eta-pratica <pratica_id>          # default: 25 ore fa
python -m app.cli forza-eta-pratica <pratica_id> --ore 30 # o un valore a scelta

# 2. Riavvia l'app: la passata immediata la ripulisce subito, senza aspettare
uvicorn app.main:app --reload
```

Comando SOLO per sviluppo/collaudo (mai contro un database di produzione: altera un timestamp reale che il job userebbe per decidere se abbandonare la pratica).

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
app/            codice applicativo (FastAPI, modelli SQLAlchemy, autenticazione, upload, estrazione)
app/cli.py          comandi amministrativi (es. reset-password)
app/estrattore.py       interfaccia astratta del motore di estrazione
app/groq_estrattore.py  implementazione Groq vision (v1)
app/codice_fiscale.py   validazione carattere di controllo e coerenza
app/verifica.py         logica della schermata di verifica campo per campo
app/pdf.py              generazione del PDF di pratica confermata
app/conferma.py         orchestrazione della conferma finale (PDF + cancellazione immagine)
app/pulizia.py          logica del job di pulizia pratiche abbandonate
app/scheduler.py        avvio/stop dello scheduler APScheduler in-process
app/templates/      pagine Jinja2 (login, dashboard, upload, verifica pratica)
storage/temp/       immagini caricate, temporanee (non tracciata da git)
tests/          test pytest
migrations/     migrazioni Alembic (schema DB)
alembic.ini         configurazione Alembic
requirements.txt   dipendenze Python
pyproject.toml      configurazione ruff/pytest/bandit
.env.example        variabili d'ambiente richieste (copiare in .env)
```

## Dati e privacy

Le immagini dei documenti caricati sono temporanee: vengono cancellate fisicamente alla conferma della pratica (dopo la generazione del PDF) o dopo 24h di abbandono. La cartella `OUTPUT_FOLDER` (PDF confermati) e il file `docform.db` vanno inclusi nel backup dell'ufficio — sono il dato definitivo del sistema. Vedi SPEC.md §2 per i dettagli.
