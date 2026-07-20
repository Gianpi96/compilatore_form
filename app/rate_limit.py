"""Rate limiting in-memory, per singolo processo (adeguato a un'istanza locale
mono-utente; non condiviso tra piu' worker/processi)."""

import math
import time
from collections import defaultdict
from threading import Lock


class LimitatoreFrequenza:
    def __init__(self, max_tentativi: int, finestra_secondi: float) -> None:
        self._max_tentativi = max_tentativi
        self._finestra_secondi = finestra_secondi
        self._tentativi: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def consenti(self, chiave: str) -> bool:
        ora = time.monotonic()
        with self._lock:
            recenti = [t for t in self._tentativi[chiave] if ora - t < self._finestra_secondi]
            if len(recenti) >= self._max_tentativi:
                self._tentativi[chiave] = recenti
                return False
            recenti.append(ora)
            self._tentativi[chiave] = recenti
            return True

    def secondi_al_prossimo_tentativo(self, chiave: str) -> int:
        ora = time.monotonic()
        with self._lock:
            recenti = [t for t in self._tentativi[chiave] if ora - t < self._finestra_secondi]
            if len(recenti) < self._max_tentativi:
                return 0
            piu_vecchio = min(recenti)
            rimanenti = self._finestra_secondi - (ora - piu_vecchio)
            return max(0, math.ceil(rimanenti))

    def reset(self) -> None:
        with self._lock:
            self._tentativi.clear()


limitatore_login = LimitatoreFrequenza(max_tentativi=5, finestra_secondi=300)
