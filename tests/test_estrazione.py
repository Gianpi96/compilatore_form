import io
import os
from datetime import date, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.pratiche as pratiche_module
from app.db import Base, get_db
from app.estrattore import EstrattoreDocumento, EstrazioneFallitaError
from app.estrazione import calcola_documento_scaduto, esegui_estrazione
from app.groq_estrattore import get_estrattore
from app.main import app
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.rate_limit import limitatore_login

EMAIL = "operatore@example.com"
PASSWORD = "password-valida-123"

CF_MARIO_ROSSI = "RSSMRA80A01H501U"


class EstrattoreFinto(EstrattoreDocumento):
    def __init__(self, risultati=None, eccezione=None):
        self._risultati = risultati
        self._eccezione = eccezione

    def estrai(self, immagine: bytes) -> dict:
        if self._eccezione is not None:
            raise self._eccezione
        return self._risultati


def _campo(valore, confidenza=0.9):
    return {"valore": valore, "confidenza": confidenza}


def _risultati_validi(**override) -> dict:
    base = {
        "nome": _campo("MARIO"),
        "cognome": _campo("ROSSI"),
        "data_nascita": _campo("1980-01-01"),
        "luogo_nascita": _campo("ROMA"),
        "codice_fiscale": _campo(CF_MARIO_ROSSI),
        "numero_documento": _campo("CA12345AB"),
        "data_scadenza": _campo("2030-01-01"),
    }
    base.update(override)
    return base


# --- test unitari su esegui_estrazione() ------------------------------------


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test_estrazione.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def pratica_caricata(db_session, tmp_path):
    utente = Utente(email="op@example.com", password_hash="hash")
    db_session.add(utente)
    db_session.commit()

    immagine = tmp_path / "documento.jpg"
    immagine.write_bytes(b"\xff\xd8\xff finto contenuto jpeg")

    pratica = Pratica(
        stato=StatoPratica.CARICATO,
        creato_da=utente.id,
        percorso_immagine_temp=str(immagine),
    )
    db_session.add(pratica)
    db_session.commit()
    return pratica


def test_estrazione_valida_popola_campi_e_stato_in_verifica(db_session, pratica_caricata):
    estrattore = EstrattoreFinto(risultati=_risultati_validi())

    esegui_estrazione(db_session, pratica_caricata, estrattore)

    assert pratica_caricata.stato == StatoPratica.IN_VERIFICA
    assert pratica_caricata.nome == "MARIO"
    assert pratica_caricata.cognome == "ROSSI"
    assert pratica_caricata.data_nascita == date(1980, 1, 1)
    assert pratica_caricata.codice_fiscale == CF_MARIO_ROSSI
    assert pratica_caricata.confidenza_json["nome"] == 0.9
    assert pratica_caricata.cf_incoerente is False

    log = (
        db_session.query(LogEvento)
        .filter_by(pratica_id=pratica_caricata.id, tipo_evento="estrazione_completata")
        .first()
    )
    assert log is not None


def test_errore_groq_porta_a_estrazione_fallita_senza_eccezione(db_session, pratica_caricata):
    estrattore = EstrattoreFinto(eccezione=EstrazioneFallitaError("errore di rete simulato"))

    esegui_estrazione(db_session, pratica_caricata, estrattore)  # non deve sollevare

    assert pratica_caricata.stato == StatoPratica.ESTRAZIONE_FALLITA
    log = (
        db_session.query(LogEvento)
        .filter_by(pratica_id=pratica_caricata.id, tipo_evento="estrazione_fallita")
        .first()
    )
    assert log is not None


def test_codice_fiscale_con_carattere_di_controllo_errato(db_session, pratica_caricata):
    risultati = _risultati_validi(codice_fiscale=_campo(CF_MARIO_ROSSI[:15] + "X"))
    estrattore = EstrattoreFinto(risultati=risultati)

    esegui_estrazione(db_session, pratica_caricata, estrattore)

    assert pratica_caricata.cf_incoerente is True


def test_data_scadenza_nel_passato_documento_scaduto(db_session, pratica_caricata):
    ieri = (date.today() - timedelta(days=1)).isoformat()
    risultati = _risultati_validi(data_scadenza=_campo(ieri))
    estrattore = EstrattoreFinto(risultati=risultati)

    esegui_estrazione(db_session, pratica_caricata, estrattore)

    assert pratica_caricata.documento_scaduto is True


def test_calcola_documento_scaduto_helper():
    assert calcola_documento_scaduto(None) is False
    assert calcola_documento_scaduto(date(2000, 1, 1)) is True


# --- test di integrazione: upload HTTP -> estrazione automatica ------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pratiche_module, "CARTELLA_TEMP", tmp_path / "storage_temp")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_estrazione_http.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine)

    db = test_session_local()
    db.add(Utente(email=EMAIL, password_hash=bcrypt.hash(PASSWORD)))
    db.commit()
    db.close()

    def override_get_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_estrattore] = lambda: EstrattoreFinto(
        risultati=_risultati_validi()
    )
    limitatore_login.reset()

    test_client = TestClient(app)
    login = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert login.status_code == 303

    yield test_client, test_session_local

    app.dependency_overrides.clear()
    engine.dispose()


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (255, 0, 0)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_upload_triggera_estrazione_automatica_fino_a_in_verifica(client):
    test_client, test_session_local = client

    risposta = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", _jpeg_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    assert risposta.status_code == 303
    pratica_id = risposta.headers["location"].rsplit("/", 1)[-1]

    db = test_session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()

    assert pratica.stato.value == "in_verifica"
    assert pratica.nome == "MARIO"
    assert pratica.confidenza_json["cognome"] == 0.9
