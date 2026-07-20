import hashlib
from pathlib import Path

import fitz  # PyMuPDF
from sqlalchemy.orm import Session

from app.models import LogEvento, Pratica, StatoPratica, Utente

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB, vedi SPEC.md §6
MAX_PAGINE_PDF = 50  # vedi SPEC.md §6

CARTELLA_TEMP = Path("storage/temp")

_ESTENSIONE_PER_TIPO = {"jpeg": "jpg", "png": "png", "pdf": "pdf"}


class FileNonValidoError(ValueError):
    """Formato file non riconosciuto (magic bytes non JPEG/PNG/PDF)."""


class FileTroppoGrandeError(ValueError):
    """File oltre il limite di dimensione consentito."""


class PdfTroppePagineError(ValueError):
    """PDF con più pagine del limite consentito."""


def rileva_tipo_file(contenuto: bytes) -> str | None:
    """Rileva il tipo reale del file dai magic bytes, mai dall'estensione dichiarata."""
    if contenuto.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if contenuto.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if contenuto.startswith(b"%PDF-"):
        return "pdf"
    return None


def valida_dimensione(contenuto: bytes) -> None:
    if len(contenuto) > MAX_UPLOAD_BYTES:
        raise FileTroppoGrandeError(
            f"Il file supera la dimensione massima di {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )


def calcola_hash(contenuto: bytes) -> str:
    return hashlib.sha256(contenuto).hexdigest()


def numero_pagine_pdf(contenuto_pdf: bytes) -> int:
    with fitz.open(stream=contenuto_pdf, filetype="pdf") as documento:
        return documento.page_count


def render_anteprime_pdf(contenuto_pdf: bytes) -> list[tuple[int, bytes]]:
    """Ritorna [(numero_pagina_1_indicizzato, png_bytes_bassa_risoluzione), ...]."""
    anteprime = []
    with fitz.open(stream=contenuto_pdf, filetype="pdf") as documento:
        for indice, pagina in enumerate(documento):
            pixmap = pagina.get_pixmap(matrix=fitz.Matrix(0.4, 0.4))
            anteprime.append((indice + 1, pixmap.tobytes("png")))
    return anteprime


def render_pagina_pdf(contenuto_pdf: bytes, numero_pagina: int) -> bytes:
    """Renderizza una singola pagina (1-indicizzata) ad alta risoluzione, come PNG."""
    with fitz.open(stream=contenuto_pdf, filetype="pdf") as documento:
        if numero_pagina < 1 or numero_pagina > documento.page_count:
            raise ValueError(f"Numero di pagina non valido: {numero_pagina}")
        pagina = documento[numero_pagina - 1]
        pixmap = pagina.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        return pixmap.tobytes("png")


def trova_pratica_duplicata(db: Session, hash_immagine: str) -> Pratica | None:
    return db.query(Pratica).filter_by(hash_immagine=hash_immagine).first()


def _salva_immagine_temp(pratica_id: str, contenuto: bytes, estensione: str) -> str:
    CARTELLA_TEMP.mkdir(parents=True, exist_ok=True)
    percorso = CARTELLA_TEMP / f"{pratica_id}.{estensione}"
    percorso.write_bytes(contenuto)
    return str(percorso)


def crea_pratica_da_upload(
    db: Session,
    utente: Utente,
    contenuto_hash: bytes,
    contenuto_immagine: bytes,
    tipo_immagine: str,
) -> Pratica:
    """Crea la Pratica, salva l'immagine in area temporanea, rileva eventuali duplicati.

    `contenuto_hash` è il file cosi' come caricato dall'operatore (per il rilevamento
    duplicati); `contenuto_immagine` e' l'immagine effettivamente da elaborare (uguale
    a `contenuto_hash` per JPEG/PNG, la pagina scelta gia' renderizzata per un PDF).
    """
    hash_immagine = calcola_hash(contenuto_hash)
    duplicata = trova_pratica_duplicata(db, hash_immagine)

    pratica = Pratica(
        stato=StatoPratica.CARICATO,
        hash_immagine=hash_immagine,
        duplicato_di_pratica_id=duplicata.id if duplicata else None,
        creato_da=utente.id,
    )
    db.add(pratica)
    db.flush()  # serve pratica.id per nominare il file

    estensione = _ESTENSIONE_PER_TIPO[tipo_immagine]
    pratica.percorso_immagine_temp = _salva_immagine_temp(
        pratica.id, contenuto_immagine, estensione
    )

    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="caricato", utente_id=utente.id))
    db.commit()
    db.refresh(pratica)
    return pratica


def annulla_pratica(db: Session, pratica: Pratica, utente: Utente) -> None:
    """Annullamento manuale (SPEC.md §5): transizione a "annullato", cancellazione
    fisica dell'immagine, pulizia dei campi anagrafici estratti. Solleva
    TransizioneNonValidaError se la pratica non è in uno stato annullabile.
    """
    pratica.transiziona(StatoPratica.ANNULLATO)

    if pratica.percorso_immagine_temp:
        Path(pratica.percorso_immagine_temp).unlink(missing_ok=True)
        pratica.percorso_immagine_temp = None

    pratica.nome = None
    pratica.cognome = None
    pratica.data_nascita = None
    pratica.luogo_nascita = None
    pratica.codice_fiscale = None
    pratica.numero_documento = None
    pratica.data_scadenza = None
    pratica.confidenza_json = None
    pratica.documento_scaduto = False
    pratica.cf_incoerente = False

    db.add(LogEvento(pratica_id=pratica.id, tipo_evento="annullato", utente_id=utente.id))
    db.commit()
