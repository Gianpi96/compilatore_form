"""CLI amministrativa. Uso:

    python -m app.cli reset-password <email> <nuova_password>
    python -m app.cli forza-eta-pratica <pratica_id> [--ore N]

Reimposta la password dell'utente esistente. Non c'e' recupero password via
email in v1 (SPEC.md §3): questo comando e' l'unico modo per recuperare
l'accesso, va eseguito localmente sulla macchina che ospita il database.

`forza-eta-pratica` e' SOLO PER SVILUPPO/COLLAUDO: sposta artificialmente
`creato_il` di una pratica nel passato, per testare senza attendere il job
di pulizia (TASK 8) o le sue soglie temporali. MAI da usare su un database
di produzione: altera un timestamp reale che il job userebbe per decidere
se abbandonare la pratica.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from passlib.hash import bcrypt

from app.db import SessionLocal
from app.models import Pratica, Utente

PASSWORD_MIN_LENGTH = 12


def reset_password(
    email: str, nuova_password: str, session_factory: Callable = SessionLocal
) -> None:
    if len(nuova_password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"La password deve avere almeno {PASSWORD_MIN_LENGTH} caratteri.")

    db = session_factory()
    try:
        utente = db.query(Utente).filter_by(email=email).first()
        if utente is None:
            raise ValueError(f"Nessun utente con email '{email}'.")
        utente.password_hash = bcrypt.hash(nuova_password)
        db.commit()
    finally:
        db.close()


def forza_eta_pratica(
    pratica_id: str, ore_fa: float, session_factory: Callable = SessionLocal
) -> datetime:
    """SOLO SVILUPPO: sposta creato_il nel passato. Ritorna il nuovo valore."""
    db = session_factory()
    try:
        pratica = db.get(Pratica, pratica_id)
        if pratica is None:
            raise ValueError(f"Nessuna pratica con id '{pratica_id}'.")
        pratica.creato_il = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=ore_fa)
        db.commit()
        return pratica.creato_il
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)

    reset = sottocomandi.add_parser("reset-password", help="Reimposta la password dell'utente")
    reset.add_argument("email")
    reset.add_argument("nuova_password")

    forza = sottocomandi.add_parser(
        "forza-eta-pratica",
        help="SOLO SVILUPPO: sposta creato_il di una pratica nel passato "
        "(per testare il job di pulizia senza aspettare)",
    )
    forza.add_argument("pratica_id")
    forza.add_argument(
        "--ore",
        type=float,
        default=25.0,
        help="ore nel passato (default: 25, oltre la soglia di 24h)",
    )

    args = parser.parse_args(argv)

    if args.comando == "reset-password":
        try:
            reset_password(args.email, args.nuova_password)
        except ValueError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
        print(f"Password aggiornata per {args.email}.")
        return 0

    if args.comando == "forza-eta-pratica":
        try:
            nuovo_creato_il = forza_eta_pratica(args.pratica_id, args.ore)
        except ValueError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
        print(f"creato_il di {args.pratica_id} impostato a {nuovo_creato_il} ({args.ore}h fa).")
        print("Riavvia l'app (o attendi il giro orario) per vedere agire il job di pulizia.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
