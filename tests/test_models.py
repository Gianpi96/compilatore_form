import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Pratica, StatoPratica, TransizioneNonValidaError, Utente


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_email_utente_deve_essere_unica(db_session):
    db_session.add(Utente(email="dup@example.com", password_hash="hash1"))
    db_session.commit()

    db_session.add(Utente(email="dup@example.com", password_hash="hash2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_transizione_confermato_a_caricato_rifiutata():
    pratica = Pratica(stato=StatoPratica.CONFERMATO, creato_da="qualsiasi-id")
    with pytest.raises(TransizioneNonValidaError):
        pratica.transiziona(StatoPratica.CARICATO)
    assert pratica.stato == StatoPratica.CONFERMATO


def test_transizione_abbandonato_e_terminale():
    pratica = Pratica(stato=StatoPratica.ABBANDONATO, creato_da="qualsiasi-id")
    with pytest.raises(TransizioneNonValidaError):
        pratica.transiziona(StatoPratica.IN_VERIFICA)


def test_transizione_caricato_a_estrazione_fallita_ammessa():
    pratica = Pratica(stato=StatoPratica.CARICATO, creato_da="qualsiasi-id")
    pratica.transiziona(StatoPratica.ESTRAZIONE_FALLITA)
    assert pratica.stato == StatoPratica.ESTRAZIONE_FALLITA


def test_transizione_estrazione_fallita_a_in_verifica_ammessa():
    pratica = Pratica(stato=StatoPratica.ESTRAZIONE_FALLITA, creato_da="qualsiasi-id")
    pratica.transiziona(StatoPratica.IN_VERIFICA)
    assert pratica.stato == StatoPratica.IN_VERIFICA


def test_transizione_in_verifica_a_estrazione_fallita_rifiutata():
    pratica = Pratica(stato=StatoPratica.IN_VERIFICA, creato_da="qualsiasi-id")
    with pytest.raises(TransizioneNonValidaError):
        pratica.transiziona(StatoPratica.ESTRAZIONE_FALLITA)
