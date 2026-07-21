import base64
import json
import os

import groq
from groq import Groq

from app.estrattore import CAMPI_ESTRATTI, EstrattoreDocumento, EstrazioneFallitaError

# Unico modello vision offerto da Groq alla data di verifica (2026-07-21,
# vedi https://console.groq.com/docs/vision); testato con una chiamata reale.
MODELLO_PREDEFINITO = "qwen/qwen3.6-27b"
TIMEOUT_SECONDI = 30

PROMPT = """Analizza l'immagine di una carta d'identità italiana ed estrai i seguenti campi.
Rispondi SOLO con un oggetto JSON, senza testo prima o dopo, con questa struttura esatta:

{
  "nome": {"valore": "...", "confidenza": 0.0},
  "cognome": {"valore": "...", "confidenza": 0.0},
  "data_nascita": {"valore": "AAAA-MM-GG", "confidenza": 0.0},
  "luogo_nascita": {"valore": "...", "confidenza": 0.0},
  "codice_fiscale": {"valore": "...", "confidenza": 0.0},
  "numero_documento": {"valore": "...", "confidenza": 0.0},
  "data_scadenza": {"valore": "AAAA-MM-GG", "confidenza": 0.0}
}

Regole:
- "valore" e' la stringa cosi' come appare sul documento; le date nel formato AAAA-MM-GG.
- "confidenza" e' un numero tra 0 e 1 che indica quanto sei sicuro della lettura.
- Se un campo non e' leggibile o assente sul documento, usa valore null e confidenza 0.
- Non includere nessun testo oltre al JSON richiesto.
"""


class GroqEstrattore(EstrattoreDocumento):
    def __init__(self, api_key: str | None = None, modello: str | None = None) -> None:
        self._client = Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self._modello = modello or os.environ.get("GROQ_MODEL", MODELLO_PREDEFINITO)

    def estrai(self, immagine: bytes) -> dict[str, dict]:
        immagine_base64 = base64.b64encode(immagine).decode("ascii")

        try:
            risposta = self._client.chat.completions.create(
                model=self._modello,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{immagine_base64}"
                                },
                            },
                        ],
                    }
                ],
                temperature=0,
                response_format={"type": "json_object"},
                timeout=TIMEOUT_SECONDI,
            )
        except groq.GroqError as exc:
            raise EstrazioneFallitaError(f"Errore nella chiamata a Groq: {exc}") from exc

        testo = risposta.choices[0].message.content
        try:
            dati = json.loads(testo)
        except (json.JSONDecodeError, TypeError) as exc:
            raise EstrazioneFallitaError("Risposta del modello non interpretabile") from exc

        if not isinstance(dati, dict):
            raise EstrazioneFallitaError("Risposta del modello non interpretabile")

        risultato = {}
        for campo in CAMPI_ESTRATTI:
            voce = dati.get(campo) or {}
            if not isinstance(voce, dict):
                voce = {}
            try:
                confidenza = float(voce.get("confidenza") or 0)
            except (TypeError, ValueError):
                confidenza = 0.0
            risultato[campo] = {
                "valore": voce.get("valore") or None,
                "confidenza": max(0.0, min(1.0, confidenza)),
            }
        return risultato


def get_estrattore() -> EstrattoreDocumento:
    """Dependency FastAPI: punto unico di creazione dell'estrattore, sostituibile
    nei test con `app.dependency_overrides`."""
    return GroqEstrattore()
