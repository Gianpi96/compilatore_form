"""Validazione del codice fiscale italiano: carattere di controllo e coerenza
con nome/cognome/data di nascita (SPEC.md §5). Il codice place (posizioni
12-15, il comune/stato di nascita) non è verificato: richiederebbe una tabella
dei codici catastali, fuori scope. L'omocodia (sostituzione cifre->lettere per
evitare collisioni tra codici altrimenti identici) non è gestita: un codice
fiscale valido ma omocodico può quindi risultare segnalato come incoerente
— comportamento accettabile perché SPEC prevede comunque conferma manuale
dell'operatore per ogni campo marcato incoerente.
"""

from datetime import date

CONSONANTI = "BCDFGHJKLMNPQRSTVWXYZ"
VOCALI = "AEIOU"

_MESI_LETTERA = ["A", "B", "C", "D", "E", "H", "L", "M", "P", "R", "S", "T"]

_CONVERSIONE_DISPARI = {
    "0": 1, "1": 0, "2": 5, "3": 7, "4": 9, "5": 13, "6": 15, "7": 17, "8": 19, "9": 21,
    "A": 1, "B": 0, "C": 5, "D": 7, "E": 9, "F": 13, "G": 15, "H": 17, "I": 19, "J": 21,
    "K": 2, "L": 4, "M": 18, "N": 20, "O": 11, "P": 3, "Q": 6, "R": 8, "S": 12, "T": 14,
    "U": 16, "V": 10, "W": 22, "X": 25, "Y": 24, "Z": 23,
}
_CONVERSIONE_PARI = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7, "I": 8, "J": 9,
    "K": 10, "L": 11, "M": 12, "N": 13, "O": 14, "P": 15, "Q": 16, "R": 17, "S": 18, "T": 19,
    "U": 20, "V": 21, "W": 22, "X": 23, "Y": 24, "Z": 25,
}
_RESTO_A_LETTERA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _consonanti(testo: str) -> list[str]:
    return [c for c in testo.upper() if c in CONSONANTI]


def _vocali(testo: str) -> list[str]:
    return [c for c in testo.upper() if c in VOCALI]


def _codice_cognome(cognome: str) -> str:
    lettere = (_consonanti(cognome) + _vocali(cognome))[:3]
    return "".join(lettere).ljust(3, "X")


def _codice_nome(nome: str) -> str:
    cons = _consonanti(nome)
    if len(cons) >= 4:
        lettere = [cons[0], cons[2], cons[3]]
    else:
        lettere = (cons + _vocali(nome))[:3]
    return "".join(lettere).ljust(3, "X")


def _codici_giorno_possibili(data_nascita: date) -> tuple[str, str]:
    return f"{data_nascita.day:02d}", f"{data_nascita.day + 40:02d}"


def calcola_carattere_controllo(primi_15: str) -> str:
    somma = 0
    for indice, carattere in enumerate(primi_15.upper()):
        posizione = indice + 1
        tabella = _CONVERSIONE_DISPARI if posizione % 2 == 1 else _CONVERSIONE_PARI
        if carattere not in tabella:
            raise ValueError(f"Carattere non valido nel codice fiscale: {carattere!r}")
        somma += tabella[carattere]
    return _RESTO_A_LETTERA[somma % 26]


def verifica_codice_fiscale(
    codice_fiscale: str | None,
    nome: str | None,
    cognome: str | None,
    data_nascita: date | None,
) -> bool:
    """True se non ci sono incoerenze rilevabili (o non c'e' nulla da verificare),
    False se il codice fiscale e' malformato, il carattere di controllo non
    corrisponde, o non e' coerente con nome/cognome/data di nascita.
    """
    if not codice_fiscale:
        return True

    codice = codice_fiscale.strip().upper()
    if len(codice) != 16 or not codice.isalnum():
        return False

    try:
        atteso = calcola_carattere_controllo(codice[:15])
    except ValueError:
        return False
    if atteso != codice[15]:
        return False

    if nome and cognome and data_nascita:
        prefisso_atteso = _codice_cognome(cognome) + _codice_nome(nome)
        anno = f"{data_nascita.year % 100:02d}"
        mese = _MESI_LETTERA[data_nascita.month - 1]
        giorno_m, giorno_f = _codici_giorno_possibili(data_nascita)

        prefisso_estratto = codice[:6]
        suffisso_data_estratto = codice[6:11]

        if prefisso_estratto != prefisso_atteso:
            return False
        if suffisso_data_estratto not in (anno + mese + giorno_m, anno + mese + giorno_f):
            return False

    return True
