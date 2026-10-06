# e-Obywatel – Home Assistant

## Stan wodomierza

Usługa `eobywatel.submit_meter_reading` przyjmuje:
- `meter_id` – ID wodomierza, np. `497`
- `reading` – nowy stan w m³

Integracja sprawdza, czy wodomierz istnieje w aktualnie pobranych danych, pobiera formularz edycji z portalu, wysyła CSRF + `_method=PUT`, a następnie odświeża dane.

**Uwaga:** endpoint zapisu został przygotowany na podstawie zarejestrowanego formularza portalu, ale wymaga działającego portalu do potwierdzenia odpowiedzi HTTP i komunikatu sukcesu.

## Faktury

Integracja zachowuje endpoint `/kkonline/platnosci` i ma konserwatywny parser `best_effort_v1`. Gdy portal zwróci prawidłową stronę, parser spróbuje wykryć wiersze faktur, daty, kwoty i linki do dokumentów.

Do precyzyjnego parsera potrzebny będzie działający HTML/HAR strony faktur.
