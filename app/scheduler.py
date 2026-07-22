"""Avvio dello scheduler del job di pulizia (TASK 8): passata immediata
all'avvio dell'app (cosi' un'immagine non resta orfana per giorni se il PC
resta spento oltre le 24h), poi ogni ora."""

import logging
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.pulizia import esegui_pulizia

logger = logging.getLogger(__name__)


def _esegui_pulizia_job(session_factory: Callable[[], Session]) -> None:
    db = session_factory()
    try:
        esegui_pulizia(db)
    finally:
        db.close()


def avvia_scheduler(session_factory: Callable[[], Session] | None = None) -> BackgroundScheduler:
    session_factory = session_factory or SessionLocal

    scheduler = BackgroundScheduler(timezone="Europe/Rome")
    scheduler.add_job(
        lambda: _esegui_pulizia_job(session_factory),
        "interval",
        hours=1,
        id="pulizia_pratiche_abbandonate",
    )
    scheduler.start()

    _esegui_pulizia_job(session_factory)  # passata immediata

    return scheduler


def ferma_scheduler(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)
