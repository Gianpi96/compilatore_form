import os
from datetime import UTC, datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cli import forza_eta_pratica
from app.db import Base
from app.models import Pratica, StatoPratica, Utente


@pytest.fixture
def session_local(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test_cli.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    yield session_local
    engine.dispose()


def test_forza_eta_pratica_sposta_creato_il_nel_passato(session_local):
    db = session_local()
    utente = Utente(email="op@example.com", password_hash="hash")
    db.add(utente)
    db.commit()

    pratica = Pratica(stato=StatoPratica.CARICATO, creato_da=utente.id)
    db.add(pratica)
    db.commit()
    pratica_id = pratica.id
    creato_il_originale = pratica.creato_il
    db.close()

    nuovo_valore = forza_eta_pratica(pratica_id, 25.0, session_factory=session_local)

    attesa = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)
    assert abs((nuovo_valore - attesa).total_seconds()) < 5
    assert nuovo_valore < creato_il_originale

    db = session_local()
    pratica_verificata = db.get(Pratica, pratica_id)
    db.close()
    assert pratica_verificata.creato_il == nuovo_valore


def test_forza_eta_pratica_id_inesistente_errore(session_local):
    with pytest.raises(ValueError, match="Nessuna pratica"):
        forza_eta_pratica("id-che-non-esiste", 25.0, session_factory=session_local)
