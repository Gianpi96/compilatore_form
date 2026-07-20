from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_401_UNAUTHORIZED,
    HTTP_413_CONTENT_TOO_LARGE,
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
from app.models import Pratica, Utente
from app.pratiche import MAX_UPLOAD_BYTES
from app.rate_limit import limitatore_login
from app.routes_pratiche import router as router_pratiche
from app.templates_setup import templates

app = FastAPI(title="DocForm")
app.include_router(router_pratiche)

# Margine per l'overhead del base64 (~33%) nel passo di scelta pagina PDF, dove
# il file originale viaggia come campo nascosto (vedi app/routes_pratiche.py).
_LIMITE_RICHIESTA_CON_BASE64 = int(MAX_UPLOAD_BYTES * 1.4)
_ROTTE_CON_LIMITE_UPLOAD = {
    "/pratiche/nuova": MAX_UPLOAD_BYTES,
    "/pratiche/nuova/pagina": _LIMITE_RICHIESTA_CON_BASE64,
}


@app.middleware("http")
async def limite_dimensione_richiesta(request: Request, call_next):
    limite = _ROTTE_CON_LIMITE_UPLOAD.get(request.url.path)
    if limite is not None and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > limite:
            return PlainTextResponse(
                "Il file supera la dimensione massima consentita (20 MB).",
                status_code=HTTP_413_CONTENT_TOO_LARGE,
            )
    return await call_next(request)


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
def dashboard(
    request: Request,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratiche = db.query(Pratica).order_by(Pratica.creato_il.desc()).all()
    return templates.TemplateResponse(
        request, "dashboard.html", {"utente": utente, "pratiche": pratiche}
    )
