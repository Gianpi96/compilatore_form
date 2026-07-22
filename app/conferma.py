"""Conferma finale di una pratica (TASK 7): genera il PDF, cancella
l'immagine, transiziona lo stato a "confermato" (SPEC.md §2, §5)."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.pdf import salva_pdf_pratica
from app.pratiche import cancella_immagine_in_modo_sicuro


def _ora_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def conferma_pratica(db: Session, pratica: Pratica, utente: Utente) -> None:
    """Precondizioni (verificate dal chiamante): pratica.stato == IN_VERIFICA
    e tutti i campi a bassa confidenza gia' confermati singolarmente."""
    pratica.confermato_il = _ora_utc()
    percorso_pdf = salva_pdf_pratica(pratica)
    pratica.percorso_pdf_output = str(percorso_pdf)

    pratica.transiziona(StatoPratica.CONFERMATO)

    immagine_cancellata = cancella_immagine_in_modo_sicuro(db, pratica)

    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="pratica_confermata", utente_id=utente.id))
    if immagine_cancellata:
        db.add(
            LogEvento(pratica_id=pratica.id, tipo_evento="immagine_cancellata", utente_id=utente.id)
        )
    db.commit()
