import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.auth import richiedi_utente
from app.conferma import conferma_pratica
from app.db import get_db
from app.estrattore import CAMPI_ESTRATTI
from app.models import LogEvento, Pratica, StatoPratica, Utente
from app.templates_setup import templates
from app.verifica import (
    ETICHETTE_CAMPI,
    SOGLIA_CONFIDENZA,
    CampoNonValidoError,
    applica_valore_campo,
    campi_da_confermare_mancanti,
    campo_e_confermato,
    compila_manualmente,
    conferma_campo_singolo,
    confidenza_campo,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _pratica_o_404(db: Session, pratica_id: str) -> Pratica:
    pratica = db.get(Pratica, pratica_id)
    if pratica is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Pratica non trovata.")
    return pratica


def _contesto_verifica(pratica: Pratica, errore: str | None = None) -> dict:
    duplicata_id = pratica.duplicato_di_pratica_id
    return {
        "pratica": pratica,
        "campi": [(campo, ETICHETTE_CAMPI[campo]) for campo in CAMPI_ESTRATTI],
        "soglia_confidenza": SOGLIA_CONFIDENZA,
        "confidenza_campo": confidenza_campo,
        "campo_e_confermato": campo_e_confermato,
        "duplicato_di_pratica_id": duplicata_id,
        "errore": errore,
    }


@router.get("/pratiche/{pratica_id}/verifica")
def verifica_pratica_form(
    request: Request,
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = _pratica_o_404(db, pratica_id)
    if pratica.stato not in (StatoPratica.IN_VERIFICA, StatoPratica.ESTRAZIONE_FALLITA):
        return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(request, "verifica.html", _contesto_verifica(pratica))


@router.post("/pratiche/{pratica_id}/verifica/campo")
def conferma_campo_route(
    request: Request,
    pratica_id: str,
    campo: str = Form(...),
    valore: str = Form(""),
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = _pratica_o_404(db, pratica_id)
    if pratica.stato != StatoPratica.IN_VERIFICA:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Pratica non in verifica.")

    try:
        applica_valore_campo(pratica, campo, valore)
        conferma_campo_singolo(pratica, campo)
    except CampoNonValidoError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="campo_confermato", utente_id=utente.id))
    db.commit()

    return templates.TemplateResponse(
        request,
        "_partials/campo_verifica.html",
        {
            "pratica": pratica,
            "campo": campo,
            "etichetta": ETICHETTE_CAMPI[campo],
            "confidenza_campo": confidenza_campo,
            "campo_e_confermato": campo_e_confermato,
            "soglia_confidenza": SOGLIA_CONFIDENZA,
        },
    )


@router.post("/pratiche/{pratica_id}/verifica/compila")
def compila_manualmente_route(
    request: Request,
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
    nome: str = Form(""),
    cognome: str = Form(""),
    data_nascita: str = Form(""),
    luogo_nascita: str = Form(""),
    codice_fiscale: str = Form(""),
    numero_documento: str = Form(""),
    data_scadenza: str = Form(""),
):
    pratica = _pratica_o_404(db, pratica_id)
    if pratica.stato != StatoPratica.ESTRAZIONE_FALLITA:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Operazione non valida.")

    valori = {
        "nome": nome,
        "cognome": cognome,
        "data_nascita": data_nascita,
        "luogo_nascita": luogo_nascita,
        "codice_fiscale": codice_fiscale,
        "numero_documento": numero_documento,
        "data_scadenza": data_scadenza,
    }
    mancanti = compila_manualmente(pratica, valori)
    if mancanti:
        return templates.TemplateResponse(
            request,
            "verifica.html",
            _contesto_verifica(
                pratica, errore=f"Compila tutti i campi obbligatori: {', '.join(mancanti)}."
            ),
            status_code=HTTP_400_BAD_REQUEST,
        )

    pratica.transiziona(StatoPratica.IN_VERIFICA)
    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="campo_confermato", utente_id=utente.id))
    db.commit()

    return RedirectResponse(f"/pratiche/{pratica.id}/verifica", status_code=HTTP_303_SEE_OTHER)


@router.post("/pratiche/{pratica_id}/verifica/conferma")
def conferma_pratica_finale(
    request: Request,
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = _pratica_o_404(db, pratica_id)
    if pratica.stato != StatoPratica.IN_VERIFICA:
        # Non e' un errore da mostrare: capita con un doppio invio (doppio
        # click, o pagina risottomessa dopo un "indietro" del browser) su una
        # pratica gia' confermata nel frattempo. Si mostra lo stato reale,
        # mai un errore JSON grezzo.
        return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)

    mancanti = campi_da_confermare_mancanti(pratica)
    if mancanti:
        return templates.TemplateResponse(
            request,
            "verifica.html",
            _contesto_verifica(
                pratica, errore=f"Conferma prima questi campi: {', '.join(mancanti)}."
            ),
            status_code=HTTP_400_BAD_REQUEST,
        )

    try:
        conferma_pratica(db, pratica, utente)
    except Exception:
        logger.exception("Errore durante la conferma della pratica %s", pratica.id)
        return templates.TemplateResponse(
            request,
            "verifica.html",
            _contesto_verifica(
                pratica, errore="Errore durante la generazione del PDF. Riprova."
            ),
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/pratiche/{pratica_id}/immagine")
def immagine_pratica(
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = _pratica_o_404(db, pratica_id)
    if not pratica.percorso_immagine_temp:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Immagine non disponibile.")

    percorso = Path(pratica.percorso_immagine_temp)
    if not percorso.exists():
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Immagine non disponibile.")

    return FileResponse(percorso)
