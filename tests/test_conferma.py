import os
from datetime import date
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import fitz
import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.conferma import conferma_pratica
from app.db import Base, get_db
from app.estrattore import CAMPI_ESTRATTI
from app.main import app
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.rate_limit import limitatore_login

CF_MARIO_ROSSI = "RSSMRA80A01H501U"
EMAIL = "operatore@example.com"
PASSWORD = "password-valida-123"


def _confidenze_alte() -> dict:
    return dict.fromkeys(CAMPI_ESTRATTI, 0.95)


def _testo_pdf(percorso: Path) -> str:
    with fitz.open(percorso) as documento:
        return "".join(pagina.get_text() for pagina in documento)


@pytest.fixture
def output_folder(tmp_path, monkeypatch):
    cartella = tmp_path / "output"
    monkeypatch.setenv("OUTPUT_FOLDER", str(cartella))
    return cartella


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test_conferma.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    yield session
    session.close()
    engine.dispose()


def _crea_pratica_pronta(
    db_session, tmp_path, utente, nome="MARIO", cognome="ROSSI", suffisso=""
) -> Pratica:
    immagine = tmp_path / f"documento{suffisso}.jpg"
    immagine.write_bytes(b"\xff\xd8\xff finto jpeg")

    pratica = Pratica(
        stato=StatoPratica.IN_VERIFICA,
        creato_da=utente.id,
        percorso_immagine_temp=str(immagine),
        nome=nome,
        cognome=cognome,
        data_nascita=date(1980, 1, 1),
        luogo_nascita="ROMA",
        codice_fiscale=CF_MARIO_ROSSI,
        numero_documento=f"CA12345{suffisso}",
        data_scadenza=date(2035, 1, 1),
        confidenza_json=_confidenze_alte(),
        campi_confermati_json=[],
    )
    db_session.add(pratica)
    db_session.commit()
    return pratica


@pytest.fixture
def pratica_pronta(db_session, tmp_path):
    utente = Utente(email="op@example.com", password_hash="hash")
    db_session.add(utente)
    db_session.commit()
    pratica = _crea_pratica_pronta(db_session, tmp_path, utente)
    return pratica, utente


# --- test unitari su conferma_pratica() -------------------------------------


def test_conferma_genera_pdf_con_contenuto_atteso(db_session, pratica_pronta, output_folder):
    pratica, utente = pratica_pronta

    conferma_pratica(db_session, pratica, utente)

    assert pratica.stato == StatoPratica.CONFERMATO
    assert pratica.percorso_pdf_output is not None

    percorso = Path(pratica.percorso_pdf_output)
    assert percorso.exists()
    assert percorso.parent == output_folder
    assert "ROSSI-MARIO" in percorso.name

    testo = _testo_pdf(percorso)
    assert "MARIO" in testo
    assert "ROSSI" in testo
    assert CF_MARIO_ROSSI in testo


def test_conferma_con_nome_gia_esistente_applica_suffisso(db_session, tmp_path, output_folder):
    utente = Utente(email="op@example.com", password_hash="hash")
    db_session.add(utente)
    db_session.commit()

    prima = _crea_pratica_pronta(db_session, tmp_path, utente, suffisso="1")
    conferma_pratica(db_session, prima, utente)

    seconda = _crea_pratica_pronta(db_session, tmp_path, utente, suffisso="2")
    conferma_pratica(db_session, seconda, utente)

    percorso_prima = Path(prima.percorso_pdf_output)
    percorso_seconda = Path(seconda.percorso_pdf_output)

    assert percorso_prima != percorso_seconda
    assert percorso_prima.exists()
    assert percorso_seconda.exists()
    assert "_2" in percorso_seconda.stem

    assert "CA123451" in _testo_pdf(percorso_prima)
    assert "CA123452" in _testo_pdf(percorso_seconda)


def test_conferma_cancella_immagine_fisicamente(db_session, pratica_pronta, output_folder):
    pratica, utente = pratica_pronta
    percorso_immagine = Path(pratica.percorso_immagine_temp)
    assert percorso_immagine.exists()

    conferma_pratica(db_session, pratica, utente)

    assert pratica.percorso_immagine_temp is None
    assert not percorso_immagine.exists()


def test_conferma_con_output_folder_non_scrivibile_non_perde_la_pratica(
    db_session, pratica_pronta, tmp_path, monkeypatch
):
    """Caso limite critico: se la scrittura del PDF fallisce, la pratica NON
    deve passare a confermato e l'immagine NON deve essere cancellata —
    altrimenti si perderebbero sia il PDF (mai scritto) sia il documento
    originale (cancellato a vuoto)."""
    pratica, utente = pratica_pronta
    percorso_immagine = Path(pratica.percorso_immagine_temp)
    assert percorso_immagine.exists()

    # Un file al posto di una cartella: qualunque tentativo di creare una
    # sottocartella qui sotto fallisce in modo portabile (non serve un vero
    # percorso inesistente tipo unità disco assente).
    ostacolo = tmp_path / "non_e_una_cartella"
    ostacolo.write_text("blocco")
    monkeypatch.setenv("OUTPUT_FOLDER", str(ostacolo / "output"))

    with pytest.raises(OSError):
        conferma_pratica(db_session, pratica, utente)

    assert pratica.stato == StatoPratica.IN_VERIFICA
    assert pratica.percorso_pdf_output is None
    assert pratica.percorso_immagine_temp == str(percorso_immagine)
    assert percorso_immagine.exists()

    eventi = db_session.query(LogEvento).filter_by(pratica_id=pratica.id).all()
    assert eventi == []


def test_log_evento_conferma_non_contiene_dati_anagrafici(
    db_session, pratica_pronta, output_folder
):
    pratica, utente = pratica_pronta

    conferma_pratica(db_session, pratica, utente)

    eventi = db_session.query(LogEvento).filter_by(pratica_id=pratica.id).all()
    tipi = {evento.tipo_evento for evento in eventi}
    assert "pratica_confermata" in tipi
    assert "immagine_cancellata" in tipi

    for evento in eventi:
        contenuto = f"{evento.pratica_id}|{evento.tipo_evento}|{evento.utente_id}"
        assert "MARIO" not in contenuto
        assert "ROSSI" not in contenuto
        assert CF_MARIO_ROSSI not in contenuto


# --- test di integrazione: endpoint HTTP ------------------------------------


@pytest.fixture
def client(tmp_path, output_folder):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_conferma_http.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine)

    db = test_session_local()
    utente = Utente(email=EMAIL, password_hash=bcrypt.hash(PASSWORD))
    db.add(utente)
    db.commit()
    utente_id = utente.id
    db.close()

    def override_get_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    limitatore_login.reset()

    test_client = TestClient(app)
    login = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert login.status_code == 303

    yield test_client, test_session_local, utente_id

    app.dependency_overrides.clear()
    engine.dispose()


def test_http_conferma_finale_genera_pdf_e_cancella_immagine(client, tmp_path, output_folder):
    test_client, session_local, utente_id = client
    db = session_local()
    immagine = tmp_path / "http_doc.jpg"
    immagine.write_bytes(b"\xff\xd8\xff finto jpeg")
    pratica = Pratica(
        stato=StatoPratica.IN_VERIFICA,
        creato_da=utente_id,
        percorso_immagine_temp=str(immagine),
        nome="ANNA",
        cognome="BIANCHI",
        data_nascita=date(1990, 6, 15),
        luogo_nascita="TORINO",
        codice_fiscale=None,
        numero_documento="XY111",
        data_scadenza=date(2030, 1, 1),
        confidenza_json=_confidenze_alte(),
        campi_confermati_json=[],
    )
    db.add(pratica)
    db.commit()
    pratica_id = pratica.id
    db.close()

    risposta = test_client.post(f"/pratiche/{pratica_id}/verifica/conferma", follow_redirects=False)
    assert risposta.status_code == 303
    assert risposta.headers["location"] == f"/pratiche/{pratica_id}"

    db = session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()
    assert pratica.stato.value == "confermato"
    assert Path(pratica.percorso_pdf_output).exists()
    assert pratica.percorso_immagine_temp is None
    assert not immagine.exists()
