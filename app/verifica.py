"""Logica della schermata di verifica e conferma campo per campo (TASK 6,
SPEC.md §5/§6/§8)."""

from app.codice_fiscale import verifica_codice_fiscale
from app.estrattore import CAMPI_ESTRATTI
from app.estrazione import calcola_documento_scaduto, parse_data_iso
from app.models import Pratica

SOGLIA_CONFIDENZA = 0.8  # SPEC.md §5: sotto questa soglia, conferma obbligatoria singola

CAMPI_DATA = {"data_nascita", "data_scadenza"}

ETICHETTE_CAMPI = {
    "nome": "Nome",
    "cognome": "Cognome",
    "data_nascita": "Data di nascita",
    "luogo_nascita": "Luogo di nascita",
    "codice_fiscale": "Codice fiscale",
    "numero_documento": "Numero documento",
    "data_scadenza": "Data di scadenza",
}


class CampoNonValidoError(ValueError):
    pass


def confidenza_campo(pratica: Pratica, campo: str) -> float:
    if not pratica.confidenza_json:
        return 0.0
    return float(pratica.confidenza_json.get(campo, 0) or 0)


def campo_e_confermato(pratica: Pratica, campo: str) -> bool:
    return bool(pratica.campi_confermati_json) and campo in pratica.campi_confermati_json


def _ricalcola_flag_derivati(pratica: Pratica) -> None:
    pratica.documento_scaduto = calcola_documento_scaduto(pratica.data_scadenza)
    pratica.cf_incoerente = not verifica_codice_fiscale(
        pratica.codice_fiscale, pratica.nome, pratica.cognome, pratica.data_nascita
    )


def applica_valore_campo(pratica: Pratica, campo: str, valore: str) -> None:
    """Salva il valore (eventualmente corretto dall'operatore) per un singolo
    campo e ricalcola i flag derivati (cf_incoerente, documento_scaduto)."""
    if campo not in CAMPI_ESTRATTI:
        raise CampoNonValidoError(f"Campo non valido: {campo}")

    valore_pulito = (valore or "").strip() or None
    if campo in CAMPI_DATA:
        setattr(pratica, campo, parse_data_iso(valore_pulito))
    else:
        setattr(pratica, campo, valore_pulito)

    _ricalcola_flag_derivati(pratica)


def conferma_campo_singolo(pratica: Pratica, campo: str) -> None:
    if campo not in CAMPI_ESTRATTI:
        raise CampoNonValidoError(f"Campo non valido: {campo}")
    confermati = set(pratica.campi_confermati_json or [])
    confermati.add(campo)
    pratica.campi_confermati_json = sorted(confermati)


def campi_da_confermare_mancanti(pratica: Pratica) -> list[str]:
    """Ritorna le etichette dei campi a bassa confidenza non ancora confermati
    singolarmente (usato per bloccare la conferma finale)."""
    return [
        ETICHETTE_CAMPI[campo]
        for campo in CAMPI_ESTRATTI
        if confidenza_campo(pratica, campo) < SOGLIA_CONFIDENZA
        and not campo_e_confermato(pratica, campo)
    ]


def compila_manualmente(pratica: Pratica, valori: dict[str, str]) -> list[str]:
    """Usato per il caso estrazione_fallita: tutti i campi sono obbligatori.
    Ritorna le etichette dei campi mancanti (lista vuota se tutto compilato,
    nel qual caso i valori sono gia' stati applicati alla pratica)."""
    valori_puliti = {campo: (valori.get(campo) or "").strip() for campo in CAMPI_ESTRATTI}
    date_parsate = {campo: parse_data_iso(valori_puliti[campo]) for campo in CAMPI_DATA}

    mancanti = [
        ETICHETTE_CAMPI[campo]
        for campo in CAMPI_ESTRATTI
        if campo not in CAMPI_DATA and not valori_puliti[campo]
    ]
    mancanti += [ETICHETTE_CAMPI[campo] for campo in CAMPI_DATA if date_parsate[campo] is None]
    if mancanti:
        return mancanti

    for campo in CAMPI_ESTRATTI:
        if campo in CAMPI_DATA:
            setattr(pratica, campo, date_parsate[campo])
        else:
            setattr(pratica, campo, valori_puliti[campo])

    pratica.confidenza_json = dict.fromkeys(CAMPI_ESTRATTI, 1.0)
    pratica.campi_confermati_json = list(CAMPI_ESTRATTI)
    _ricalcola_flag_derivati(pratica)
    return []
