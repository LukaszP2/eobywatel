# e-Obywatel (KKOnline) – Home Assistant Custom Component

![Version](https://img.shields.io/badge/version-0.6.0-blue)
![HACS](https://img.shields.io/badge/HACS-Custom-orange)
![License](https://img.shields.io/badge/License-MIT-green)

*(🇬🇧 English version below)*

## 🇵🇱 Opis
Nieoficjalna integracja z portalem e-Obywatel (KKOnline) dla Home Assistant. Pozwala na automatyczne pobieranie informacji o wodomierzach, powiadomieniach, fakturach oraz zarządzanie subskrypcjami powiadomień.

### Główne funkcje
- **Wodomierze**: Pobieranie odczytów, historii, daty legalizacji oraz wysyłanie nowych odczytów z poziomu HA.
- **Powiadomienia**: Informacja o nowych, nieprzeczytanych wiadomościach, najbliższych terminach. Możliwość odznaczania wszystkich jako przeczytane.
- **Subskrypcje**: Możliwość zarządzania subskrypcjami bezpośrednio przez encje typu *Switch*.
- **Faktury**: Sprawdzanie dostępności i statusu opłat.

### Instalacja przez HACS (Custom Repository)
Wymagane jest dostarczenie struktury katalogów zgodnej z HACS. Z racji struktury tego repozytorium, najprostszą metodą jest pobranie przygotowanego pliku `.zip` z zakładki **Releases**.
1. Otwórz HACS w Home Assistant.
2. Kliknij trzy kropki -> "Niestandardowe repozytoria".
3. Wklej link do repozytorium na GitHubie i wybierz kategorię **Integracja**.
4. Zainstaluj integrację i uruchom ponownie Home Assistant.

### Użycie
Usługa `eobywatel.submit_meter_reading` przyjmuje:
- `meter_id` – ID wodomierza, np. `497`
- `reading` – nowy stan w m³

---

## 🇬🇧 Description
Unofficial e-Obywatel (KKOnline) integration for Home Assistant. It allows you to automatically pull information about water meters, notifications, invoices, and manage notification subscriptions.

### Features
- **Water Meters**: Reading values, history, legalization dates, and submitting new readings from HA.
- **Notifications**: Info about new, unread messages. Mark all as read functionality.
- **Subscriptions**: Manage notification subscriptions directly via *Switch* entities.
- **Invoices**: Checking invoice availability and status.

### Installation via HACS (Custom Repository)
1. Open HACS in Home Assistant.
2. Click the three dots -> "Custom repositories".
3. Paste the URL of this repository and select **Integration**.
4. Install and restart Home Assistant.

### Usage
The service `eobywatel.submit_meter_reading` takes:
- `meter_id` – water meter ID, e.g. `497`
- `reading` – new state in m³
