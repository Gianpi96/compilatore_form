import os
from datetime import date

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.rate_limit import limitatore_login

EMAIL = "operatore@example.com"
PASSWORD = "password-valida-123"

CF_MARIO_ROSSI = "RSSMRA80A01H501U"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_verifica.db'}", connect_args={"check_same_thread": False}
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


def _crea_pratica_in_verifica(session_local, utente_id, tmp_path, confidenze=None) -> str:
    db = session_local()
    immagine = tmp_path / "documento.jpg"
    if not immagine.exists():
        immagine.write_bytes(b"\xff\xd8\xff finto jpeg")

    pratica = Pratica(
        stato=StatoPratica.IN_VERIFICA,
        creato_da=utente_id,
        percorso_immagine_temp=str(immagine),
        nome="MARIO",
        cognome="ROSSI",
        data_nascita=date(1980, 1, 1),
        luogo_nascita="ROMA",
        codice_fiscale=CF_MARIO_ROSSI,
        numero_documento="CA12345AB",
        data_scadenza=date(2035, 1, 1),
        confidenza_json=confidenze
        or {
            "nome": 0.95,
            "cognome": 0.95,
            "data_nascita": 0.95,
            "luogo_nascita": 0.5,
            "codice_fiscale": 0.95,
            "numero_documento": 0.6,
            "data_scadenza": 0.95,
        },
    )
    db.add(pratica)
    db.commit()
    pratica_id = pratica.id
    db.close()
    return pratica_id


def _crea_pratica_estrazione_fallita(session_local, utente_id, tmp_path) -> str:
    db = session_local()
    immagine = tmp_path / "documento_fallito.jpg"
    if not immagine.exists():
        immagine.write_bytes(b"\xff\xd8\xff finto jpeg")

    pratica = Pratica(
        stato=StatoPratica.ESTRAZIONE_FALLITA,
        creato_da=utente_id,
        percorso_immagine_temp=str(immagine),
    )
    db.add(pratica)
    db.commit()
    pratica_id = pratica.id
    db.close()
    return pratica_id


# --- redirect dal dettaglio -------------------------------------------------


def test_dettaglio_redirige_a_verifica_se_in_verifica(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    risposta = test_client.get(f"/pratiche/{pratica_id}", follow_redirects=False)
    assert risposta.status_code == 303
    assert risposta.headers["location"] == f"/pratiche/{pratica_id}/verifica"


# --- conferma finale bloccata finche' i campi gialli non sono confermati ----


def test_conferma_finale_rifiutata_senza_conferma_campi_gialli(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    risposta = test_client.post(f"/pratiche/{pratica_id}/verifica/conferma")

    assert risposta.status_code == 400
    assert "Luogo di nascita" in risposta.text
    assert "Numero documento" in risposta.text

    db = session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()
    assert pratica.stato == StatoPratica.IN_VERIFICA


def test_conferma_finale_accettata_dopo_conferma_di_tutti_i_campi_gialli(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    for campo, valore in [("luogo_nascita", "ROMA"), ("numero_documento", "CA12345AB")]:
        r = test_client.post(
            f"/pratiche/{pratica_id}/verifica/campo", data={"campo": campo, "valore": valore}
        )
        assert r.status_code == 200
        assert "confermato" in r.text

    risposta = test_client.post(f"/pratiche/{pratica_id}/verifica/conferma", follow_redirects=False)
    assert risposta.status_code == 303
    assert risposta.headers["location"] == f"/pratiche/{pratica_id}"

    db = session_local()
    pratica = db.get(Pratica, pratica_id)
    log = (
        db.query(LogEvento)
        .filter_by(pratica_id=pratica_id, tipo_evento="pratica_confermata")
        .first()
    )
    db.close()
    assert pratica.stato == StatoPratica.CONFERMATO
    assert log is not None


def test_doppio_invio_conferma_dopo_gia_confermata_non_espone_json_grezzo(client, tmp_path):
    """Regressione: un secondo invio (doppio click, o pagina risottomessa dopo
    un 'indietro' del browser) su una pratica gia' confermata non deve mai
    mostrare un errore JSON grezzo — solo un redirect alla pratica reale."""
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    for campo, valore in [("luogo_nascita", "ROMA"), ("numero_documento", "CA12345AB")]:
        test_client.post(
            f"/pratiche/{pratica_id}/verifica/campo", data={"campo": campo, "valore": valore}
        )

    prima = test_client.post(f"/pratiche/{pratica_id}/verifica/conferma", follow_redirects=False)
    assert prima.status_code == 303

    seconda = test_client.post(f"/pratiche/{pratica_id}/verifica/conferma", follow_redirects=False)
    assert seconda.status_code == 303
    assert seconda.headers["location"] == f"/pratiche/{pratica_id}"
    assert "application/json" not in seconda.headers.get("content-type", "")


def test_conferma_campo_invalido_rifiutata(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    risposta = test_client.post(
        f"/pratiche/{pratica_id}/verifica/campo",
        data={"campo": "non_esiste", "valore": "x"},
    )
    assert risposta.status_code == 400


# --- estrazione_fallita: tutti obbligatori, nessuna evidenziazione gialla ---


def test_estrazione_fallita_nessuna_evidenziazione_gialla_tutti_obbligatori(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_estrazione_fallita(session_local, utente_id, tmp_path)

    risposta = test_client.get(f"/pratiche/{pratica_id}/verifica")
    assert risposta.status_code == 200
    assert "confidenza" not in risposta.text.lower()
    assert risposta.text.count("required") == 7


def test_estrazione_fallita_campo_mancante_rifiutato(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_estrazione_fallita(session_local, utente_id, tmp_path)

    risposta = test_client.post(
        f"/pratiche/{pratica_id}/verifica/compila",
        data={
            "nome": "LUIGI",
            "cognome": "VERDI",
            "data_nascita": "1975-05-05",
            "luogo_nascita": "MILANO",
            "codice_fiscale": "VRDLGU75E05F205X",
            "numero_documento": "",
            "data_scadenza": "2032-01-01",
        },
    )
    assert risposta.status_code == 400
    assert "Numero documento" in risposta.text

    db = session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()
    assert pratica.stato == StatoPratica.ESTRAZIONE_FALLITA


def test_estrazione_fallita_compilazione_completa_procede_a_in_verifica(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_estrazione_fallita(session_local, utente_id, tmp_path)

    risposta = test_client.post(
        f"/pratiche/{pratica_id}/verifica/compila",
        data={
            "nome": "LUIGI",
            "cognome": "VERDI",
            "data_nascita": "1975-05-05",
            "luogo_nascita": "MILANO",
            "codice_fiscale": "VRDLGU75E05F205X",
            "numero_documento": "CB98765XY",
            "data_scadenza": "2032-01-01",
        },
        follow_redirects=False,
    )
    assert risposta.status_code == 303
    assert risposta.headers["location"] == f"/pratiche/{pratica_id}/verifica"

    db = session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()
    assert pratica.stato == StatoPratica.IN_VERIFICA
    assert pratica.nome == "LUIGI"
    # tutti i campi inseriti manualmente sono gia' considerati confermati
    assert set(pratica.campi_confermati_json) == set(pratica.confidenza_json.keys())


# --- immagine ----------------------------------------------------------------


def test_immagine_pratica_servita_correttamente(client, tmp_path):
    test_client, session_local, utente_id = client
    pratica_id = _crea_pratica_in_verifica(session_local, utente_id, tmp_path)

    risposta = test_client.get(f"/pratiche/{pratica_id}/immagine")
    assert risposta.status_code == 200
    assert risposta.content.startswith(b"\xff\xd8\xff")


def test_immagine_pratica_inesistente_404(client, tmp_path):
    test_client, session_local, utente_id = client
    db = session_local()
    pratica = Pratica(stato=StatoPratica.CARICATO, creato_da=utente_id, percorso_immagine_temp=None)
    db.add(pratica)
    db.commit()
    pratica_id = pratica.id
    db.close()

    risposta = test_client.get(f"/pratiche/{pratica_id}/immagine")
    assert risposta.status_code == 404
