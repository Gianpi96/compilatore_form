"""Generazione del PDF di pratica confermata (TASK 7, SPEC.md §5).

Naming: AAAA-MM-GG_COGNOME-NOME.pdf (data odierna in Europe/Rome), con
suffisso numerico incrementale se il nome esiste già in OUTPUT_FOLDER.
Scrittura atomica: si scrive su un file temporaneo nella stessa cartella,
poi si rinomina — mai un file parziale visibile con il nome finale.
"""

import io
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.estrattore import CAMPI_ESTRATTI
from app.models import Pratica
from app.verifica import ETICHETTE_CAMPI

OUTPUT_FOLDER_DEFAULT = "./output"


def cartella_output() -> Path:
    return Path(os.environ.get("OUTPUT_FOLDER", OUTPUT_FOLDER_DEFAULT))


def _sanitizza_per_nome_file(valore: str) -> str:
    pulito = "".join(c if c.isalnum() else "-" for c in valore.upper())
    while "--" in pulito:
        pulito = pulito.replace("--", "-")
    return pulito.strip("-") or "SCONOSCIUTO"


def nome_base_pdf(pratica: Pratica) -> str:
    oggi = datetime_oggi_roma()
    cognome = _sanitizza_per_nome_file(pratica.cognome or "")
    nome = _sanitizza_per_nome_file(pratica.nome or "")
    return f"{oggi.isoformat()}_{cognome}-{nome}"


def datetime_oggi_roma() -> date:
    return datetime.now(ZoneInfo("Europe/Rome")).date()


def percorso_pdf_disponibile(cartella: Path, base: str) -> Path:
    candidato = cartella / f"{base}.pdf"
    if not candidato.exists():
        return candidato
    contatore = 2
    while True:
        candidato = cartella / f"{base}_{contatore}.pdf"
        if not candidato.exists():
            return candidato
        contatore += 1


def genera_contenuto_pdf(pratica: Pratica) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _larghezza, altezza = A4

    y = altezza - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "DocForm - Pratica confermata")
    y -= 30

    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Id pratica: {pratica.id}")
    y -= 15
    if pratica.confermato_il:
        c.drawString(50, y, f"Confermata il: {pratica.confermato_il.strftime('%d/%m/%Y %H:%M')}")
    y -= 30

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, "Dati anagrafici")
    y -= 20

    c.setFont("Helvetica", 11)
    for campo in CAMPI_ESTRATTI:
        etichetta = ETICHETTE_CAMPI[campo]
        valore = getattr(pratica, campo)
        if isinstance(valore, date):
            valore_testo = valore.strftime("%d/%m/%Y")
        else:
            valore_testo = valore if valore else "(non disponibile)"
        c.drawString(50, y, f"{etichetta}: {valore_testo}")
        y -= 18

    c.showPage()
    c.save()
    return buffer.getvalue()


def _scrivi_atomico(percorso: Path, contenuto: bytes) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    fd, percorso_temp = tempfile.mkstemp(dir=percorso.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenuto)
        os.replace(percorso_temp, percorso)
    except Exception:
        Path(percorso_temp).unlink(missing_ok=True)
        raise


def salva_pdf_pratica(pratica: Pratica) -> Path:
    cartella = cartella_output()
    base = nome_base_pdf(pratica)
    percorso = percorso_pdf_disponibile(cartella, base)
    contenuto = genera_contenuto_pdf(pratica)
    _scrivi_atomico(percorso, contenuto)
    return percorso
