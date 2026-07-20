import os

from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Utente

SESSION_COOKIE_NAME = "docform_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60  # 8 ore, coerente con un turno d'ufficio


class SessioneNonValidaError(Exception):
    """Sollevata quando manca una sessione valida su una pagina protetta."""


def _serializer() -> URLSafeTimedSerializer:
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY non configurata: vedi .env.example")
    return URLSafeTimedSerializer(secret_key, salt="docform-session")


def crea_token_sessione(utente_id: str) -> str:
    return _serializer().dumps({"utente_id": utente_id})


def _leggi_utente_id_da_token(token: str) -> str | None:
    try:
        dati = _serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return dati.get("utente_id")


def verifica_credenziali(db: Session, email: str, password: str) -> Utente | None:
    utente = db.query(Utente).filter_by(email=email).first()
    if utente is None:
        return None
    if not bcrypt.verify(password, utente.password_hash):
        return None
    return utente


def get_utente_opzionale(request: Request, db: Session = Depends(get_db)) -> Utente | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    utente_id = _leggi_utente_id_da_token(token)
    if utente_id is None:
        return None
    return db.get(Utente, utente_id)


def richiedi_utente(utente: Utente | None = Depends(get_utente_opzionale)) -> Utente:
    if utente is None:
        raise SessioneNonValidaError()
    return utente


def imposta_cookie_sessione(response: Response, request: Request, utente_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=crea_token_sessione(utente_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


def cancella_cookie_sessione(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
