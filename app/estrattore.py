from abc import ABC, abstractmethod

CAMPI_ESTRATTI = (
    "nome",
    "cognome",
    "data_nascita",
    "luogo_nascita",
    "codice_fiscale",
    "numero_documento",
    "data_scadenza",
)


class EstrazioneFallitaError(Exception):
    """L'estrazione non ha prodotto un risultato utilizzabile (SPEC.md §5)."""


class EstrattoreDocumento(ABC):
    """Interfaccia intercambiabile per il motore di estrazione (SPEC.md §1, TASK 5).

    v1 usa GroqEstrattore (vedi app/groq_estrattore.py); un'eventuale
    implementazione locale futura rispetta la stessa interfaccia, senza
    richiedere modifiche ai chiamanti.
    """

    @abstractmethod
    def estrai(self, immagine: bytes) -> dict[str, dict]:
        """Ritorna {campo: {"valore": str | None, "confidenza": float 0-1}}
        per ciascun campo in CAMPI_ESTRATTI. Solleva EstrazioneFallitaError se
        l'estrazione non produce un risultato utilizzabile (errore di rete,
        timeout, risposta non interpretabile)."""
