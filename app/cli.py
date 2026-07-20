"""CLI amministrativa. Uso:

    python -m app.cli reset-password <email> <nuova_password>

Reimposta la password dell'utente esistente. Non c'e' recupero password via
email in v1 (SPEC.md §3): questo comando e' l'unico modo per recuperare
l'accesso, va eseguito localmente sulla macchina che ospita il database.
"""

import argparse
import sys
from collections.abc import Callable

from passlib.hash import bcrypt

from app.db import SessionLocal
from app.models import Utente

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)

    reset = sottocomandi.add_parser("reset-password", help="Reimposta la password dell'utente")
    reset.add_argument("email")
    reset.add_argument("nuova_password")

    args = parser.parse_args(argv)

    if args.comando == "reset-password":
        try:
            reset_password(args.email, args.nuova_password)
        except ValueError as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
        print(f"Password aggiornata per {args.email}.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
