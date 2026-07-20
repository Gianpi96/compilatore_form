"""Seed di SVILUPPO: crea un utente di test.

MAI eseguire su un database di produzione: e' pensato solo per popolare
un docform.db locale durante lo sviluppo, dopo aver applicato le migrazioni
con `alembic upgrade head`. Non e' invocato automaticamente dall'app.

Uso:
    python -m app.seed_dev
"""

from passlib.hash import bcrypt

from app.db import SessionLocal
from app.models import Utente

EMAIL_DEV = "dev@example.com"
PASSWORD_DEV = "password-sviluppo-123"  # nosec B105 - credenziale di sviluppo nota, mai in produzione


def seed() -> None:
    db = SessionLocal()
    try:
        esistente = db.query(Utente).filter_by(email=EMAIL_DEV).first()
        if esistente:
            print(f"Utente di sviluppo gia' presente: {EMAIL_DEV}")
            return

        utente = Utente(email=EMAIL_DEV, password_hash=bcrypt.hash(PASSWORD_DEV))
        db.add(utente)
        db.commit()
        print(f"Utente di sviluppo creato: {EMAIL_DEV} / {PASSWORD_DEV}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
