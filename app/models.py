import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _nuovo_id() -> str:
    return str(uuid.uuid4())


def _ora_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class StatoPratica(enum.StrEnum):
    CARICATO = "caricato"
    IN_VERIFICA = "in_verifica"
    CONFERMATO = "confermato"
    ESTRAZIONE_FALLITA = "estrazione_fallita"
    ABBANDONATO = "abbandonato"
    ANNULLATO = "annullato"


# Transizioni ammesse (vedi SPEC.md §2 e §5, confermate con l'utente per il caso
# estrazione_fallita: si raggiunge da "caricato" subito dopo un tentativo di
# estrazione fallito, non da "in_verifica" — coerente con TASK 5).
TRANSIZIONI_AMMESSE: dict[StatoPratica, set[StatoPratica]] = {
    StatoPratica.CARICATO: {
        StatoPratica.IN_VERIFICA,
        StatoPratica.ESTRAZIONE_FALLITA,
        StatoPratica.ABBANDONATO,
        StatoPratica.ANNULLATO,
    },
    StatoPratica.IN_VERIFICA: {
        StatoPratica.CONFERMATO,
        StatoPratica.ABBANDONATO,
        StatoPratica.ANNULLATO,
    },
    StatoPratica.ESTRAZIONE_FALLITA: {
        StatoPratica.IN_VERIFICA,
        StatoPratica.ABBANDONATO,
        StatoPratica.ANNULLATO,
    },
    StatoPratica.CONFERMATO: set(),
    StatoPratica.ABBANDONATO: set(),
    StatoPratica.ANNULLATO: set(),
}


class TransizioneNonValidaError(ValueError):
    pass


class Utente(Base):
    __tablename__ = "utente"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_nuovo_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    creato_il: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_ora_utc)

    pratiche: Mapped[list["Pratica"]] = relationship(
        "Pratica", back_populates="creatore", foreign_keys="Pratica.creato_da"
    )


class Pratica(Base):
    __tablename__ = "pratica"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_nuovo_id)
    stato: Mapped[StatoPratica] = mapped_column(
        SAEnum(StatoPratica, native_enum=False, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=StatoPratica.CARICATO,
    )

    nome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cognome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_nascita: Mapped[Date | None] = mapped_column(Date, nullable=True)
    luogo_nascita: Mapped[str | None] = mapped_column(String(255), nullable=True)
    codice_fiscale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    numero_documento: Mapped[str | None] = mapped_column(String(50), nullable=True)
    data_scadenza: Mapped[Date | None] = mapped_column(Date, nullable=True)

    confidenza_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    documento_scaduto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cf_incoerente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    hash_immagine: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    duplicato_di_pratica_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("pratica.id"), nullable=True
    )

    percorso_immagine_temp: Mapped[str | None] = mapped_column(String(500), nullable=True)
    percorso_pdf_output: Mapped[str | None] = mapped_column(String(500), nullable=True)

    creato_da: Mapped[str] = mapped_column(String(36), ForeignKey("utente.id"), nullable=False)
    creato_il: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_ora_utc)
    confermato_il: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    creatore: Mapped["Utente"] = relationship(
        "Utente", back_populates="pratiche", foreign_keys=[creato_da]
    )
    duplicato_di: Mapped["Pratica | None"] = relationship(
        "Pratica", remote_side=[id], foreign_keys=[duplicato_di_pratica_id]
    )
    log_eventi: Mapped[list["LogEvento"]] = relationship("LogEvento", back_populates="pratica")

    def transiziona(self, nuovo_stato: StatoPratica) -> None:
        consentiti = TRANSIZIONI_AMMESSE.get(self.stato, set())
        if nuovo_stato not in consentiti:
            raise TransizioneNonValidaError(
                f"Transizione non ammessa: {self.stato.value} -> {nuovo_stato.value}"
            )
        self.stato = nuovo_stato


class LogEvento(Base):
    __tablename__ = "log_evento"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_nuovo_id)
    pratica_id: Mapped[str] = mapped_column(String(36), ForeignKey("pratica.id"), nullable=False)
    tipo_evento: Mapped[str] = mapped_column(String(100), nullable=False)
    utente_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("utente.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_ora_utc)

    pratica: Mapped["Pratica"] = relationship("Pratica", back_populates="log_eventi")


class Impostazione(Base):
    __tablename__ = "impostazione"

    chiave: Mapped[str] = mapped_column(String(100), primary_key=True)
    valore: Mapped[str] = mapped_column(String(1000), nullable=False)
    aggiornato_il: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_ora_utc, onupdate=_ora_utc
    )
