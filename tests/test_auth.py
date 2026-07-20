import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-non-usare-in-produzione")

import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cli import reset_password
from app.db import Base, get_db
from app.main import app
from app.models import Utente
from app.rate_limit import limitatore_login

EMAIL = "operatore@example.com"
PASSWORD = "password-valida-123"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_auth.db'}", connect_args={"check_same_thread": False}
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

    yield TestClient(app), test_session_local

    app.dependency_overrides.clear()
    engine.dispose()


def test_login_credenziali_valide_crea_sessione_e_redirige(client):
    test_client, _ = client
    risposta = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert risposta.status_code == 303
    assert risposta.headers["location"] == "/dashboard"
    cookie = risposta.headers["set-cookie"]
    assert "docform_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=strict" in cookie.lower()


def test_login_credenziali_errate_401_nessuna_sessione(client):
    test_client, _ = client
    risposta = test_client.post(
        "/login", data={"email": EMAIL, "password": "sbagliata"}, follow_redirects=False
    )
    assert risposta.status_code == 401
    assert "set-cookie" not in risposta.headers


def test_dashboard_senza_sessione_redirige_a_login(client):
    test_client, _ = client
    risposta = test_client.get("/dashboard", follow_redirects=False)
    assert risposta.status_code == 303
    assert risposta.headers["location"] == "/login"


def test_dashboard_con_sessione_valida_200(client):
    test_client, _ = client
    login = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert login.status_code == 303

    risposta = test_client.get("/dashboard")
    assert risposta.status_code == 200
    assert EMAIL in risposta.text


def test_password_mai_in_chiaro_nel_db(client):
    _, test_session_local = client
    db = test_session_local()
    utente = db.query(Utente).filter_by(email=EMAIL).first()
    db.close()
    assert utente.password_hash != PASSWORD
    assert utente.password_hash.startswith("$2b$")


def test_reset_password_cli_e_verifica_con_nuovo_login(client):
    test_client, test_session_local = client
    reset_password(EMAIL, "nuova-password-lunga-123", session_factory=test_session_local)

    vecchia = test_client.post(
        "/login", data={"email": EMAIL, "password": PASSWORD}, follow_redirects=False
    )
    assert vecchia.status_code == 401

    nuova = test_client.post(
        "/login",
        data={"email": EMAIL, "password": "nuova-password-lunga-123"},
        follow_redirects=False,
    )
    assert nuova.status_code == 303


def test_reset_password_rifiuta_password_troppo_corta(client):
    _, test_session_local = client
    with pytest.raises(ValueError):
        reset_password(EMAIL, "corta", session_factory=test_session_local)


def test_rate_limit_blocca_dopo_troppi_tentativi(client):
    test_client, _ = client
    for _ in range(5):
        risposta = test_client.post("/login", data={"email": EMAIL, "password": "sbagliata"})
        assert risposta.status_code == 401

    risposta = test_client.post("/login", data={"email": EMAIL, "password": "sbagliata"})
    assert risposta.status_code == 429
    assert int(risposta.headers["retry-after"]) > 0
    assert "countdown" in risposta.text
