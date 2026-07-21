import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.codice_fiscale import verifica_codice_fiscale
from app.estrattore import EstrattoreDocumento, EstrazioneFallitaError
from app.models import LogEvento, Pratica, StatoPratica

logger = logging.getLogger(__name__)


def parse_data_iso(valore: str | None) -> date | None:
    if not valore:
        return None
    try:
        return date.fromisoformat(valore)
    except ValueError:
        return None


def calcola_documento_scaduto(data_scadenza: date | None) -> bool:
    if data_scadenza is None:
        return False
    oggi_roma = datetime.now(ZoneInfo("Europe/Rome")).date()
    return data_scadenza < oggi_roma


def esegui_estrazione(db: Session, pratica: Pratica, estrattore: EstrattoreDocumento) -> None:
    """Invia l'immagine della pratica al motore di estrazione e aggiorna i campi
    (TASK 5). In caso di errore/timeout/risposta inutilizzabile, porta la
    pratica in stato estrazione_fallita senza sollevare eccezioni (SPEC.md §5).
    """
    try:
        immagine = Path(pratica.percorso_immagine_temp).read_bytes()
        risultati = estrattore.estrai(immagine)
    except (EstrazioneFallitaError, OSError) as exc:
        # Il messaggio d'errore qui riguarda sempre l'API/il file, mai dati
        # anagrafici: a questo punto della pipeline non sono ancora stati letti.
        logger.warning("Estrazione fallita per pratica %s: %s", pratica.id, exc)
        pratica.transiziona(StatoPratica.ESTRAZIONE_FALLITA)
        db.add(
            LogEvento(
                pratica_id=pratica.id, tipo_evento="estrazione_fallita", utente_id=pratica.creato_da
            )
        )
        db.commit()
        return

    pratica.nome = risultati["nome"]["valore"]
    pratica.cognome = risultati["cognome"]["valore"]
    pratica.data_nascita = parse_data_iso(risultati["data_nascita"]["valore"])
    pratica.luogo_nascita = risultati["luogo_nascita"]["valore"]
    pratica.codice_fiscale = risultati["codice_fiscale"]["valore"]
    pratica.numero_documento = risultati["numero_documento"]["valore"]
    pratica.data_scadenza = parse_data_iso(risultati["data_scadenza"]["valore"])
    pratica.confidenza_json = {campo: voce["confidenza"] for campo, voce in risultati.items()}

    pratica.documento_scaduto = calcola_documento_scaduto(pratica.data_scadenza)
    pratica.cf_incoerente = not verifica_codice_fiscale(
        pratica.codice_fiscale, pratica.nome, pratica.cognome, pratica.data_nascita
    )

    pratica.transiziona(StatoPratica.IN_VERIFICA)
    db.add(
        LogEvento(
            pratica_id=pratica.id, tipo_evento="estrazione_completata", utente_id=pratica.creato_da
        )
    )
    db.commit()
