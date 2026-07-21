from datetime import date

from app.codice_fiscale import calcola_carattere_controllo, verifica_codice_fiscale

CF_MARIO_ROSSI = "RSSMRA80A01H501U"


def test_carattere_controllo_esempio_noto():
    assert calcola_carattere_controllo(CF_MARIO_ROSSI[:15]) == "U"


def test_codice_fiscale_valido_e_coerente():
    assert verifica_codice_fiscale(CF_MARIO_ROSSI, "Mario", "Rossi", date(1980, 1, 1)) is True


def test_carattere_controllo_errato_incoerente():
    cf_alterato = CF_MARIO_ROSSI[:15] + "A"  # carattere di controllo sbagliato
    assert cf_alterato[15] != CF_MARIO_ROSSI[15]
    assert verifica_codice_fiscale(cf_alterato, "Mario", "Rossi", date(1980, 1, 1)) is False


def test_nome_non_coerente_con_codice_fiscale():
    assert verifica_codice_fiscale(CF_MARIO_ROSSI, "Luigi", "Rossi", date(1980, 1, 1)) is False


def test_data_nascita_non_coerente_con_codice_fiscale():
    assert verifica_codice_fiscale(CF_MARIO_ROSSI, "Mario", "Rossi", date(1985, 3, 15)) is False


def test_lunghezza_non_valida_incoerente():
    assert verifica_codice_fiscale("RSSMRA80A01H501", "Mario", "Rossi", date(1980, 1, 1)) is False


def test_codice_fiscale_assente_nessuna_incoerenza():
    assert verifica_codice_fiscale(None, "Mario", "Rossi", date(1980, 1, 1)) is True


def test_codice_femmina_giorno_piu_40():
    # Maria Verdi, nata 1 gennaio 1980: giorno codificato come 01+40=41
    primi_15 = "VRDMRA80A41H501"
    cf = primi_15 + calcola_carattere_controllo(primi_15)
    assert verifica_codice_fiscale(cf, "Maria", "Verdi", date(1980, 1, 1)) is True
