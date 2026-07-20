import base64
import io
import os
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import fitz
import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.pratiche as pratiche_module
from app.db import Base, get_db
from app.main import app
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.rate_limit import limitatore_login

EMAIL = "operatore@example.com"
PASSWORD = "password-valida-123"


def _jpeg_bytes(colore: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), colore).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pdf_bytes(numero_pagine: int) -> bytes:
    documento = fitz.open()
    for indice in range(numero_pagine):
        pagina = documento.new_page()
        pagina.insert_text((72, 72), f"Documento di test - pagina {indice + 1}")
    contenuto = documento.tobytes()
    documento.close()
    return contenuto


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pratiche_module, "CARTELLA_TEMP", tmp_path / "storage_temp")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_pratiche.db'}", connect_args={"check_same_thread": False}
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
    limitatore_login.reset()

    test_client = TestClient(app)
    login = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert login.status_code == 303

    yield test_client, test_session_local

    app.dependency_overrides.clear()
    engine.dispose()


def test_upload_jpeg_valido_crea_pratica_caricato(client):
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

    assert pratica is not None
    assert pratica.stato.value == "caricato"
    assert pratica.percorso_immagine_temp is not None
    assert Path(pratica.percorso_immagine_temp).exists()


def test_upload_file_troppo_grande_rifiutato(client):
    test_client, _ = client
    contenuto_grande = b"\xff\xd8\xff" + b"0" * (21 * 1024 * 1024)
    risposta = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", contenuto_grande, "image/jpeg")},
    )
    assert risposta.status_code == 413
    assert "dimensione massima" in risposta.text.lower()


def test_upload_exe_rinominato_jpg_rifiutato(client):
    test_client, _ = client
    contenuto_exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
    risposta = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", contenuto_exe, "image/jpeg")},
    )
    assert risposta.status_code == 415
    assert "non valido" in risposta.text.lower()


def test_upload_pdf_tre_pagine_scelta_pagina_2(client):
    test_client, test_session_local = client
    pdf = _pdf_bytes(3)

    risposta_scelta = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.pdf", pdf, "application/pdf")},
    )
    assert risposta_scelta.status_code == 200
    assert "Pagina 1" in risposta_scelta.text
    assert "Pagina 3" in risposta_scelta.text

    pdf_base64 = base64.b64encode(pdf).decode("ascii")
    risposta_conferma = test_client.post(
        "/pratiche/nuova/pagina",
        data={"pdf_base64": pdf_base64, "pagina": "2"},
        follow_redirects=False,
    )
    assert risposta_conferma.status_code == 303
    pratica_id = risposta_conferma.headers["location"].rsplit("/", 1)[-1]

    db = test_session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()

    immagine_salvata = Path(pratica.percorso_immagine_temp).read_bytes()
    render_pagina_2 = pratiche_module.render_pagina_pdf(pdf, 2)
    render_pagina_1 = pratiche_module.render_pagina_pdf(pdf, 1)
    render_pagina_3 = pratiche_module.render_pagina_pdf(pdf, 3)

    assert immagine_salvata == render_pagina_2
    assert immagine_salvata != render_pagina_1
    assert immagine_salvata != render_pagina_3


def test_upload_hash_duplicato_segnalato_ma_completato(client):
    test_client, test_session_local = client
    contenuto = _jpeg_bytes()

    prima = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", contenuto, "image/jpeg")},
        follow_redirects=False,
    )
    prima_id = prima.headers["location"].rsplit("/", 1)[-1]

    seconda = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", contenuto, "image/jpeg")},
        follow_redirects=False,
    )
    assert seconda.status_code == 303
    seconda_id = seconda.headers["location"].rsplit("/", 1)[-1]
    assert seconda_id != prima_id

    db = test_session_local()
    pratica_seconda = db.get(Pratica, seconda_id)
    db.close()
    assert pratica_seconda.duplicato_di_pratica_id == prima_id

    dettaglio = test_client.get(f"/pratiche/{seconda_id}")
    assert dettaglio.status_code == 200
    assert "duplicato" in dettaglio.text.lower()


def _carica_pratica_di_test(test_client) -> str:
    risposta = test_client.post(
        "/pratiche/nuova",
        files={"file": ("carta.jpg", _jpeg_bytes(), "image/jpeg")},
        follow_redirects=False,
    )
    return risposta.headers["location"].rsplit("/", 1)[-1]


def test_annulla_pratica_caricato_pulisce_campi_e_cancella_immagine(client):
    test_client, test_session_local = client
    pratica_id = _carica_pratica_di_test(test_client)

    db = test_session_local()
    percorso_immagine = Path(db.get(Pratica, pratica_id).percorso_immagine_temp)
    db.close()
    assert percorso_immagine.exists()

    risposta = test_client.post(f"/pratiche/{pratica_id}/annulla", follow_redirects=False)
    assert risposta.status_code == 303
    assert risposta.headers["location"] == f"/pratiche/{pratica_id}"

    db = test_session_local()
    pratica = db.get(Pratica, pratica_id)
    log_annullato = (
        db.query(LogEvento).filter_by(pratica_id=pratica_id, tipo_evento="annullato").first()
    )
    db.close()

    assert pratica.stato.value == "annullato"
    assert pratica.percorso_immagine_temp is None
    assert not percorso_immagine.exists()
    assert pratica.nome is None
    assert pratica.codice_fiscale is None
    assert log_annullato is not None


def test_annulla_pratica_gia_confermata_rifiutato(client):
    test_client, test_session_local = client
    pratica_id = _carica_pratica_di_test(test_client)

    db = test_session_local()
    pratica = db.get(Pratica, pratica_id)
    pratica.stato = StatoPratica.CONFERMATO  # TASK 6/7 non ancora implementati: bypass diretto
    db.commit()
    db.close()

    risposta = test_client.post(f"/pratiche/{pratica_id}/annulla")
    assert risposta.status_code == 400

    db = test_session_local()
    pratica = db.get(Pratica, pratica_id)
    db.close()
    assert pratica.stato.value == "confermato"


def test_annulla_pratica_gia_annullata_rifiutato(client):
    test_client, _ = client
    pratica_id = _carica_pratica_di_test(test_client)

    prima = test_client.post(f"/pratiche/{pratica_id}/annulla", follow_redirects=False)
    assert prima.status_code == 303

    seconda = test_client.post(f"/pratiche/{pratica_id}/annulla")
    assert seconda.status_code == 400
