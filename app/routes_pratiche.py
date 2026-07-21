import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_303_SEE_OTHER,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
)

from app.auth import richiedi_utente
from app.db import get_db
from app.estrattore import EstrattoreDocumento
from app.estrazione import esegui_estrazione
from app.groq_estrattore import get_estrattore
from app.models import Pratica, StatoPratica, TransizioneNonValidaError, Utente
from app.pratiche import (
    MAX_PAGINE_PDF,
    FileTroppoGrandeError,
    annulla_pratica,
    crea_pratica_da_upload,
    numero_pagine_pdf,
    render_anteprime_pdf,
    render_pagina_pdf,
    rileva_tipo_file,
    valida_dimensione,
)
from app.templates_setup import templates

router = APIRouter()


@router.get("/pratiche/nuova")
def nuova_pratica_form(request: Request, utente: Utente = Depends(richiedi_utente)):
    return templates.TemplateResponse(request, "nuova_pratica.html", {})


@router.post("/pratiche/nuova")
async def nuova_pratica_upload(
    request: Request,
    file: UploadFile = File(...),
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
    estrattore: EstrattoreDocumento = Depends(get_estrattore),
):
    contenuto = await file.read()

    try:
        valida_dimensione(contenuto)
    except FileTroppoGrandeError as exc:
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": str(exc)},
            status_code=HTTP_413_CONTENT_TOO_LARGE,
        )

    tipo = rileva_tipo_file(contenuto)
    if tipo is None:
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": "Formato non valido. Sono ammessi solo file JPEG, PNG o PDF."},
            status_code=HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    if tipo == "pdf":
        try:
            pagine = numero_pagine_pdf(contenuto)
        except Exception:
            return templates.TemplateResponse(
                request,
                "nuova_pratica.html",
                {"errore": "Il PDF non è leggibile."},
                status_code=HTTP_400_BAD_REQUEST,
            )
        if pagine > MAX_PAGINE_PDF:
            return templates.TemplateResponse(
                request,
                "nuova_pratica.html",
                {"errore": f"Il PDF ha troppe pagine (massimo {MAX_PAGINE_PDF})."},
                status_code=HTTP_400_BAD_REQUEST,
            )
        anteprime = [
            (numero, base64.b64encode(png).decode("ascii"))
            for numero, png in render_anteprime_pdf(contenuto)
        ]
        pdf_base64 = base64.b64encode(contenuto).decode("ascii")
        return templates.TemplateResponse(
            request,
            "scegli_pagina.html",
            {"anteprime": anteprime, "pdf_base64": pdf_base64},
        )

    pratica = crea_pratica_da_upload(db, utente, contenuto, contenuto, tipo)
    esegui_estrazione(db, pratica, estrattore)
    return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)


@router.post("/pratiche/nuova/pagina")
def scegli_pagina_submit(
    request: Request,
    pdf_base64: str = Form(...),
    pagina: int = Form(...),
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
    estrattore: EstrattoreDocumento = Depends(get_estrattore),
):
    try:
        contenuto_pdf = base64.b64decode(pdf_base64, validate=True)
    except Exception:
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": "Richiesta non valida."},
            status_code=HTTP_400_BAD_REQUEST,
        )

    try:
        valida_dimensione(contenuto_pdf)
    except FileTroppoGrandeError as exc:
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": str(exc)},
            status_code=HTTP_413_CONTENT_TOO_LARGE,
        )

    if rileva_tipo_file(contenuto_pdf) != "pdf":
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": "Richiesta non valida."},
            status_code=HTTP_400_BAD_REQUEST,
        )

    try:
        immagine_pagina = render_pagina_pdf(contenuto_pdf, pagina)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "nuova_pratica.html",
            {"errore": "Pagina selezionata non valida."},
            status_code=HTTP_400_BAD_REQUEST,
        )

    pratica = crea_pratica_da_upload(db, utente, contenuto_pdf, immagine_pagina, "png")
    esegui_estrazione(db, pratica, estrattore)
    return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)


@router.get("/pratiche/{pratica_id}")
def dettaglio_pratica(
    request: Request,
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = db.get(Pratica, pratica_id)
    if pratica is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Pratica non trovata.")

    if pratica.stato in (StatoPratica.IN_VERIFICA, StatoPratica.ESTRAZIONE_FALLITA):
        return RedirectResponse(f"/pratiche/{pratica.id}/verifica", status_code=HTTP_303_SEE_OTHER)

    duplicata = None
    if pratica.duplicato_di_pratica_id:
        duplicata = db.get(Pratica, pratica.duplicato_di_pratica_id)

    return templates.TemplateResponse(
        request, "pratica_dettaglio.html", {"pratica": pratica, "duplicata": duplicata}
    )


@router.post("/pratiche/{pratica_id}/annulla")
def annulla_pratica_route(
    request: Request,
    pratica_id: str,
    utente: Utente = Depends(richiedi_utente),
    db: Session = Depends(get_db),
):
    pratica = db.get(Pratica, pratica_id)
    if pratica is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Pratica non trovata.")

    try:
        annulla_pratica(db, pratica, utente)
    except TransizioneNonValidaError:
        return templates.TemplateResponse(
            request,
            "pratica_dettaglio.html",
            {
                "pratica": pratica,
                "duplicata": None,
                "errore": "Questa pratica non può essere annullata nel suo stato attuale.",
            },
            status_code=HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(f"/pratiche/{pratica.id}", status_code=HTTP_303_SEE_OTHER)
