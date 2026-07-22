"""Job di pulizia pratiche abbandonate (TASK 8, SPEC.md §2/§5/§6).

Marca come "abbandonato" le pratiche non confermate da più di 24h, cancella
l'immagine e ripulisce i campi anagrafici — stessa logica di minimizzazione
dell'annullamento manuale (app/pratiche.py), ma per timeout invece che per
scelta esplicita dell'operatore (utente_id nullo nel LogEvento).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import LogEvento, Pratica, StatoPratica
from app.pratiche import cancella_immagine_in_modo_sicuro, ripulisci_campi_anagrafici

logger = logging.getLogger(__name__)

SOGLIA_ABBANDONO_ORE = 24

# Stati terminali: mai toccati dal job (transizione non ammessa o già ripuliti).
STATI_NON_ABBANDONABILI = (
    StatoPratica.CONFERMATO,
    StatoPratica.ABBANDONATO,
    StatoPratica.ANNULLATO,
)


def _ora_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def trova_pratiche_da_abbandonare(db: Session) -> list[Pratica]:
    limite = _ora_utc() - timedelta(hours=SOGLIA_ABBANDONO_ORE)
    return (
        db.query(Pratica)
        .filter(Pratica.creato_il < limite)
        .filter(~Pratica.stato.in_(STATI_NON_ABBANDONABILI))
        .all()
    )


def abbandona_pratica(db: Session, pratica: Pratica) -> None:
    pratica.transiziona(StatoPratica.ABBANDONATO)
    cancella_immagine_in_modo_sicuro(db, pratica)
    ripulisci_campi_anagrafici(pratica)
    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="abbandonato", utente_id=None))


def esegui_pulizia(db: Session) -> int:
    """Esegue una passata di pulizia. Ritorna il numero di pratiche abbandonate.

    Ogni pratica viene commit-ata singolarmente: un problema su una non deve
    far perdere il lavoro già fatto sulle altre nella stessa passata.
    """
    pratiche = trova_pratiche_da_abbandonare(db)
    contatore = 0
    for pratica in pratiche:
        try:
            abbandona_pratica(db, pratica)
            db.commit()
            contatore += 1
        except Exception:
            logger.exception("Errore durante l'abbandono della pratica %s", pratica.id)
            db.rollback()

    if contatore:
        logger.info("Job di pulizia: %d pratica/e abbandonata/e.", contatore)
    return contatore
