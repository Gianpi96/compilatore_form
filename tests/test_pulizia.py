import os
from datetime import date, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.pulizia import esegui_pulizia
from app.scheduler import avvia_scheduler, ferma_scheduler


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test_pulizia.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    yield session, session_local
    session.close()
    engine.dispose()


def _crea_pratica(
    db_session, utente, tmp_path, *, ore_fa: float, stato: StatoPratica, suffisso: str
):
    immagine = tmp_path / f"documento{suffisso}.jpg"
    immagine.write_bytes(b"\xff\xd8\xff finto jpeg")

    pratica = Pratica(
        stato=stato,
        creato_da=utente.id,
        percorso_immagine_temp=str(immagine),
        nome="MARIO",
        cognome="ROSSI",
        data_nascita=date(1980, 1, 1),
        creato_il=_ora_utc() - timedelta(hours=ore_fa),
    )
    db_session.add(pratica)
    db_session.commit()
    return pratica, immagine


def _ora_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)


# --- test unitari su esegui_pulizia() ---------------------------------------


def test_pratica_25h_fa_viene_abbandonata(db_session, tmp_path):
    session, _ = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    pratica, immagine = _crea_pratica(
        session, utente, tmp_path, ore_fa=25, stato=StatoPratica.CARICATO, suffisso="1"
    )
    assert immagine.exists()

    contatore = esegui_pulizia(session)

    session.refresh(pratica)
    assert contatore == 1
    assert pratica.stato == StatoPratica.ABBANDONATO
    assert pratica.nome is None
    assert pratica.cognome is None
    assert pratica.data_nascita is None
    assert pratica.percorso_immagine_temp is None
    assert not immagine.exists()

    log = (
        session.query(LogEvento)
        .filter_by(pratica_id=pratica.id, tipo_evento="abbandonato")
        .first()
    )
    assert log is not None
    assert log.utente_id is None


def test_pratica_1h_fa_non_toccata(db_session, tmp_path):
    session, _ = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    pratica, immagine = _crea_pratica(
        session, utente, tmp_path, ore_fa=1, stato=StatoPratica.CARICATO, suffisso="1"
    )

    contatore = esegui_pulizia(session)

    session.refresh(pratica)
    assert contatore == 0
    assert pratica.stato == StatoPratica.CARICATO
    assert pratica.nome == "MARIO"
    assert immagine.exists()


def test_pratica_confermata_non_toccata_anche_se_vecchia(db_session, tmp_path):
    session, _ = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    pratica, immagine = _crea_pratica(
        session, utente, tmp_path, ore_fa=48, stato=StatoPratica.CONFERMATO, suffisso="1"
    )

    contatore = esegui_pulizia(session)

    session.refresh(pratica)
    assert contatore == 0
    assert pratica.stato == StatoPratica.CONFERMATO
    assert pratica.nome == "MARIO"
    assert immagine.exists()


def test_pratica_annullata_non_toccata_anche_se_vecchia(db_session, tmp_path):
    session, _ = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    pratica, _immagine = _crea_pratica(
        session, utente, tmp_path, ore_fa=48, stato=StatoPratica.ANNULLATO, suffisso="1"
    )

    contatore = esegui_pulizia(session)

    session.refresh(pratica)
    assert contatore == 0
    assert pratica.stato == StatoPratica.ANNULLATO


def test_piu_pratiche_vecchie_vengono_abbandonate_in_una_passata(db_session, tmp_path):
    session, _ = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    _crea_pratica(
        session, utente, tmp_path, ore_fa=30, stato=StatoPratica.CARICATO, suffisso="1"
    )
    _crea_pratica(
        session, utente, tmp_path, ore_fa=25, stato=StatoPratica.IN_VERIFICA, suffisso="2"
    )
    _crea_pratica(
        session, utente, tmp_path, ore_fa=26, stato=StatoPratica.ESTRAZIONE_FALLITA, suffisso="3"
    )
    _crea_pratica(session, utente, tmp_path, ore_fa=1, stato=StatoPratica.CARICATO, suffisso="4")

    contatore = esegui_pulizia(session)
    assert contatore == 3


# --- passata immediata all'avvio dello scheduler ----------------------------


def test_avvia_scheduler_esegue_passata_immediata(db_session, tmp_path):
    session, session_local = db_session
    utente = Utente(email="op@example.com", password_hash="hash")
    session.add(utente)
    session.commit()

    pratica, immagine = _crea_pratica(
        session, utente, tmp_path, ore_fa=25, stato=StatoPratica.CARICATO, suffisso="1"
    )
    pratica_id = pratica.id
    session.close()

    scheduler = avvia_scheduler(session_factory=session_local)
    try:
        verifica = session_local()
        pratica_ripulita = verifica.get(Pratica, pratica_id)
        assert pratica_ripulita.stato == StatoPratica.ABBANDONATO
        assert not immagine.exists()
        verifica.close()

        job = scheduler.get_job("pulizia_pratiche_abbandonate")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 3600
    finally:
        ferma_scheduler(scheduler)
