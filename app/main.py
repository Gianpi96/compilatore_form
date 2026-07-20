from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_401_UNAUTHORIZED,
    HTTP_429_TOO_MANY_REQUESTS,
)

from app.auth import (
    SessioneNonValidaError,
    cancella_cookie_sessione,
    imposta_cookie_sessione,
    richiedi_utente,
    verifica_credenziali,
)
from app.db import get_db
from app.models import Utente
from app.rate_limit import limitatore_login

app = FastAPI(title="DocForm")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.exception_handler(SessioneNonValidaError)
def _redirect_a_login(request: Request, exc: SessioneNonValidaError) -> RedirectResponse:
    return RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip_client = request.client.host if request.client else "sconosciuto"
    if not limitatore_login.consenti(ip_client):
        secondi_attesa = limitatore_login.secondi_al_prossimo_tentativo(ip_client)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "errore": "Troppi tentativi.",
                "email": email,
                "retry_after_secondi": secondi_attesa,
            },
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(secondi_attesa)},
        )

    utente = verifica_credenziali(db, email, password)
    if utente is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"errore": "Email o password non corrette.", "email": email},
            status_code=HTTP_401_UNAUTHORIZED,
        )

    risposta = RedirectResponse("/dashboard", status_code=HTTP_303_SEE_OTHER)
    imposta_cookie_sessione(risposta, request, utente.id)
    return risposta


@app.post("/logout")
def logout():
    risposta = RedirectResponse("/login", status_code=HTTP_303_SEE_OTHER)
    cancella_cookie_sessione(risposta)
    return risposta


@app.get("/dashboard")
def dashboard(request: Request, utente: Utente = Depends(richiedi_utente)):
    return templates.TemplateResponse(request, "dashboard.html", {"utente": utente})
