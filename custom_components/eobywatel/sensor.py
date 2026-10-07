from __future__ import annotations

import html as html_lib
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    INVOICES_PATH,
    LOGIN_PAGE,
    LOGIN_PATH,
    METER_EDIT_PATH,
    METER_UPDATE_PATH,
    NOTIFICATION_RESULTS,
    SUBSCRIPTIONS_PAGE,
    WATER_LIST,
)

_LOGGER = logging.getLogger(__name__)


def clean(value: str | None) -> str:
    value = html_lib.unescape(value or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def csrf_from_html(page: str) -> str | None:
    patterns = (
        r'name=["\']_csrf_token["\'][^>]*value=["\']([^"\']+)',
        r'value=["\']([^"\']+)["\'][^>]*name=["\']_csrf_token["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def parse_number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:[,.]\d+)?", value.replace("\xa0", " "))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_water_list(page: str, base: str) -> list[dict]:
    meters: list[dict] = []
    soup = BeautifulSoup(page, "html.parser")
    for header in soup.select("h3.ui-accordion-header"):
        signature = header.select_one("span.form-signature")
        point = clean(signature.get_text(" ", strip=True) if signature else "")
        htext = clean(header.get_text(" ", strip=True))
        address = clean(htext.replace(point, "", 1)) if point else htext
        content = header.find_next_sibling()
        if not content:
            continue
        content_text = clean(content.get_text(" ", strip=True))
        detail_id = None
        detail_link = content.find("a", href=re.compile(r"/woda/punktodbioru/\d+$"))
        if detail_link:
            match = re.search(r"/woda/punktodbioru/(\d+)$", detail_link.get("href", ""))
            if match:
                detail_id = match.group(1)
        for link in content.find_all("a", href=re.compile(r"/woda/punktodbioru/stanlicznika/\d+/edit")):
            href = link.get("href", "")
            meter_match = re.search(r"/stanlicznika/(\d+)/edit", href)
            if not meter_match:
                continue
            meter_id = meter_match.group(1)
            name_match = re.search(
                r"Woda\s+(.+?)(?=\s+Aktualizuj stan wodomierza|\s+Ostatni znany stan|$)",
                content_text,
                re.IGNORECASE,
            )
            reading_match = re.search(
                r"Ostatni znany stan licznika:\s*([\d\s.,]+)\s*m\s*3",
                content_text,
                re.IGNORECASE,
            )
            date_match = re.search(r"Data odczytu:\s*(\d{2}\.\d{2}\.\d{4})", content_text, re.IGNORECASE)
            reading = clean(reading_match.group(1)) if reading_match else None
            meters.append(
                {
                    "name": clean(name_match.group(1)) if name_match else "Woda",
                    "reading": reading,
                    "reading_value": parse_number(reading),
                    "reading_date": clean(date_match.group(1)) if date_match else None,
                    "point": point,
                    "address": address,
                    "point_id": detail_id,
                    "meter_id": meter_id,
                    "url": urljoin(base, href),
                }
            )
    result: list[dict] = []
    seen: set[str] = set()
    for meter in meters:
        if meter["meter_id"] in seen:
            continue
        seen.add(meter["meter_id"])
        result.append(meter)
    return result


def parse_water_detail(page: str) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    text = soup.get_text(" ", strip=True)
    result: dict = {"history": []}
    match = re.search(r"Numer identyfikacyjny punktu odbioru:\s*(\d+)", text, re.IGNORECASE)
    if match:
        result["point_number"] = match.group(1)
    match = re.search(r"Ostatni znany stan licznika:\s*([\d\s.,]+)\s*m\s*3", text, re.IGNORECASE)
    if match:
        result["reading"] = clean(match.group(1))
    match = re.search(r"Data odczytu:\s*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if match:
        result["reading_date"] = match.group(1)
    match = re.search(r"Legalizacja do:\s*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if match:
        result["legalization_date"] = match.group(1)
    for tr in soup.find_all("tr"):
        cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) >= 2 and re.fullmatch(r"\d{2}-\d{2}-\d{4}", cells[0]) and parse_number(cells[1]) is not None:
            result["history"].append({"date": cells[0], "reading": parse_number(cells[1])})
    if not result["history"]:
        for date, value in re.findall(r"(\d{2}-\d{2}-\d{4})\s+([\d\s.,]+)\s*m\s*3", text):
            result["history"].append({"date": date, "reading": parse_number(value)})
    return result


def parse_notifications(payload: dict) -> list[dict]:
    result: list[dict] = []
    for row in payload.get("data", []):
        body = BeautifulSoup(row.get("wiadomosc", ""), "html.parser").get_text(" ", strip=True)
        short = BeautifulSoup(row.get("skrot", ""), "html.parser").get_text(" ", strip=True)
        sent = row.get("data_wyslania") or {}
        result.append(
            {
                "id": row.get("id"),
                "title": clean(row.get("tytul", "")),
                "body": clean(body),
                "short": clean(short),
                "read": bool(row.get("czyOdczytana")),
                "channels": [x.get("nazwa") for x in row.get("kanaly", []) if x.get("nazwa")],
                "timestamp": sent.get("timestamp") if isinstance(sent, dict) else None,
            }
        )
    return result


def parse_invoices(page: str, base: str) -> dict:
    """Parsuje listę faktur z odpowiedniej tabeli na stronie /kkonline/."""
    soup = BeautifulSoup(page, "html.parser")
    items: list[dict] = []

    # Szukamy tabeli faktur
    tables = soup.find_all("table", class_=re.compile(r"sortowana-tabela"))
    target_table = None
    
    for table in tables:
        thead = table.find("thead")
        if thead and "Nr faktury" in thead.get_text():
            target_table = table
            break
            
    if target_table:
        tbody = target_table.find("tbody")
        if tbody:
            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                
                # Oczekujemy co najmniej 6 kolumn:
                # 0: checkbox, 1: Nr faktury, 2: Status, 3: Data wystawienia, 4: Termin płatności, 5: Kwota
                if len(cells) >= 6:
                    nr_faktury = clean(cells[1].get_text(strip=True))
                    status = clean(cells[2].get_text(strip=True))
                    data_wystawienia = clean(cells[3].get_text(strip=True))
                    termin_platnosci = clean(cells[4].get_text(strip=True))
                    kwota = clean(cells[5].get_text(strip=True))
                    
                    item = {
                        "nr_faktury": nr_faktury,
                        "status": status,
                        "data_wystawienia": data_wystawienia,
                        "termin_platnosci": termin_platnosci,
                        "kwota": kwota,
                        # Dodajemy stare klucze dla kompatybilności z UI (jeśli integracja ich używała)
                        "text": f"Faktura {nr_faktury} ({status})",
                        "date": termin_platnosci,
                        "amount": kwota,
                    }
                    
                    if len(cells) >= 7:
                        link = cells[6].find("a", href=True)
                        if link:
                            item["url"] = urljoin(base, link.get("href"))
                            
                    items.append(item)

    return {
        "status": "available",
        "count": len(items),
        "items": items[:50],
        "parser": "html_table_v2",
    }


def parse_subscriptions(page: str) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    categories: list[dict] = []
    labels = {lab.get("for"): clean(lab.get_text(" ", strip=True)) for lab in soup.find_all("label")}
    for inp in soup.select('input[type="checkbox"][name*="kategorie"]'):
        categories.append(
            {
                "id": inp.get("value"),
                "name": labels.get(inp.get("id"), clean(inp.parent.get_text(" ", strip=True))),
                "enabled": inp.has_attr("checked"),
            }
        )
    return {"categories": categories, "enabled": [c["name"] for c in categories if c["enabled"]]}


class EobywatelCoordinator(DataUpdateCoordinator[dict]):
    """Single portal coordinator. The portal has no push channel, so HA polls it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self._submission_store = Store(hass, 1, f"{DOMAIN}.meter_submissions_{entry.entry_id}")
        self.submission_state: dict[str, dict] = {}

    async def async_load_submission_state(self) -> None:
        data = await self._submission_store.async_load()
        if isinstance(data, dict):
            self.submission_state = data
        else:
            self.submission_state = {}

    async def _save_submission_state(self) -> None:
        await self._submission_store.async_save(self.submission_state)

    async def _login_session(self) -> tuple[aiohttp.ClientSession, str]:
        base = self.entry.data[CONF_URL].rstrip("/") + "/"
        session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            timeout=aiohttp.ClientTimeout(total=45),
            headers={"User-Agent": "eobywatel-homeassistant/0.4"},
        )
        try:
            async with session.get(urljoin(base, LOGIN_PAGE)) as response:
                html = await response.text()
                response.raise_for_status()
            csrf = csrf_from_html(html)
            if not csrf:
                raise UpdateFailed("Nie znaleziono tokenu CSRF na stronie logowania")
            async with session.post(
                urljoin(base, LOGIN_PATH),
                data={
                    "_csrf_token": csrf,
                    "_username": self.entry.data[CONF_USERNAME],
                    "_password": self.entry.data[CONF_PASSWORD],
                },
                allow_redirects=True,
            ) as response:
                home = await response.text()
                response.raise_for_status()
                if response.url.path.rstrip("/") == "/login" or "Strona logowania" in home:
                    raise UpdateFailed("Logowanie nieudane")
            return session, base
        except Exception:
            await session.close()
            raise

    async def _async_update_data(self) -> dict:
        session, base = await self._login_session()
        try:
            _LOGGER.debug("eobywatel: logowanie OK; odświeżanie danych (interwał 12 h)")
            async with session.get(urljoin(base, WATER_LIST)) as response:
                water_html = await response.text()
                response.raise_for_status()
            meters = parse_water_list(water_html, base)
            points: dict[str, dict] = {}
            for meter in meters:
                point_id = meter.get("point_id") or f"unknown-{meter['meter_id']}"
                points.setdefault(
                    point_id,
                    {
                        "point_id": meter.get("point_id"),
                        "point": meter.get("point"),
                        "address": meter.get("address"),
                        "meters": [],
                    },
                )["meters"].append(meter)

            for point_id, point in points.items():
                if not point.get("point_id"):
                    continue
                try:
                    async with session.get(urljoin(base, f"/woda/punktodbioru/{point['point_id']}")) as response:
                        detail_html = await response.text()
                        response.raise_for_status()
                    detail = parse_water_detail(detail_html)
                    point.update(detail)
                    for meter in point["meters"]:
                        if detail.get("legalization_date"):
                            meter["legalization_date"] = detail["legalization_date"]
                        meter["history"] = detail.get("history", [])
                        if detail.get("reading") is not None:
                            meter["reading"] = detail["reading"]
                            meter["reading_value"] = parse_number(detail["reading"])
                        if detail.get("reading_date") is not None:
                            meter["reading_date"] = detail["reading_date"]
                except aiohttp.ClientError as err:
                    _LOGGER.warning("eobywatel: szczegóły punktu %s niedostępne: %s", point_id, err)

            notifications: list[dict] = []
            try:
                params = {
                    "draw": "1",
                    "start": "0",
                    "length": "100",
                    "search[value]": "",
                    "search[regex]": "false",
                    "order[0][column]": "0",
                    "order[0][dir]": "asc",
                }
                async with session.get(urljoin(base, NOTIFICATION_RESULTS), params=params) as response:
                    payload = json.loads(await response.text())
                    response.raise_for_status()
                notifications = parse_notifications(payload)
            except (aiohttp.ClientError, json.JSONDecodeError) as err:
                _LOGGER.warning("eobywatel: nie udało się pobrać wiadomości: %s", err)

            subscriptions = {"categories": [], "enabled": []}
            try:
                async with session.get(urljoin(base, SUBSCRIPTIONS_PAGE)) as response:
                    subscription_html = await response.text()
                    response.raise_for_status()
                subscriptions = parse_subscriptions(subscription_html)
            except aiohttp.ClientError as err:
                _LOGGER.warning("eobywatel: nie udało się pobrać subskrypcji: %s", err)

            invoices = {
                "status": "unknown",
                "count": 0,
                "items": [],
                "url": urljoin(base, INVOICES_PATH),
            }
            try:
                async with session.get(urljoin(base, INVOICES_PATH)) as response:
                    invoice_html = await response.text()
                    invoices["status_code"] = response.status
                    invoices["available"] = response.status < 400
                    if response.status < 400:
                        parsed_invoices = parse_invoices(invoice_html, base)
                        invoices.update(parsed_invoices)
                        invoices["preview"] = clean(
                            BeautifulSoup(invoice_html, "html.parser").get_text(" ", strip=True)
                        )[:500]
                    else:
                        invoices["status"] = "portal_error"
            except aiohttp.ClientError as err:
                invoices["status"] = "unavailable"
                invoices["error"] = str(err)

            meters_flat = [meter for point in points.values() for meter in point["meters"]]
            _LOGGER.debug(
                "eobywatel: %d wodomierzy, %d wiadomości (%d nieprzeczytanych), %d subskrypcji ON",
                len(meters_flat),
                len(notifications),
                sum(not n["read"] for n in notifications),
                len(subscriptions.get("enabled", [])),
            )
            return {
                "meters": meters_flat,
                "points": list(points.values()),
                "notifications": notifications,
                "subscriptions": subscriptions,
                "invoices": invoices,
                "status": "online",
                "last_update_interval_hours": 12,
            }
        except UpdateFailed:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Błąd komunikacji z e-Obywatelem: {err}") from err
        finally:
            await session.close()

    async def submit_meter_reading(self, meter_id: str, reading: float) -> None:
        """Submit a new meter reading through the portal form."""
        meter_id = str(meter_id).strip()
        if not meter_id:
            raise ValueError("ID wodomierza nie może być puste")
        if reading < 0:
            raise ValueError("Stan wodomierza nie może być ujemny")

        known_meter = next(
            (
                meter
                for meter in (self.data or {}).get("meters", [])
                if str(meter.get("meter_id")) == meter_id
            ),
            None,
        )
        if not known_meter:
            raise ValueError(f"Wodomierz {meter_id} nie został znaleziony")

        session, base = await self._login_session()
        try:
            edit_url = urljoin(base, METER_EDIT_PATH.format(meter_id=meter_id))
            async with session.get(edit_url) as response:
                page = await response.text()
                response.raise_for_status()
            soup = BeautifulSoup(page, "html.parser")
            form = soup.find("form")
            if not form:
                raise UpdateFailed(f"Nie znaleziono formularza wodomierza {meter_id}")
            token_el = form.find("input", attrs={"name": re.compile(r"\[_token\]$")})
            token = token_el.get("value") if token_el else None
            if not token:
                raise UpdateFailed("Nie znaleziono tokenu CSRF formularza wodomierza")
            field = "hsi_wodabundle_aktualizujstanwodomierza[ostatniZnanyStanZuzycia]"
            token_field = "hsi_wodabundle_aktualizujstanwodomierza[_token]"
            data = {
                "_method": "PUT",
                field: str(reading).replace(".", ","),
                token_field: token,
            }
            async with session.post(
                urljoin(base, METER_UPDATE_PATH.format(meter_id=meter_id)),
                data=data,
                allow_redirects=True,
            ) as response:
                body = await response.text()
                response.raise_for_status()
                if "Błąd" in body or "blad" in body.lower():
                    raise UpdateFailed(
                        f"Portal zwrócił możliwy błąd przy zapisie wodomierza {meter_id}"
                    )

            _LOGGER.debug(
                "eobywatel: wysłano stan wodomierza %s = %.3f m³; weryfikacja",
                meter_id,
                reading,
            )
            await self.async_refresh()
            verified_meter = next(
                (m for m in (self.data or {}).get("meters", [])
                 if str(m.get("meter_id")) == meter_id),
                None,
            )
            verified = (
                verified_meter is not None
                and verified_meter.get("reading_value") is not None
                and abs(float(verified_meter["reading_value"]) - reading) < 0.0005
            )
            now = dt_util.utcnow().isoformat()
            self.submission_state[meter_id] = {
                "reading": reading,
                "timestamp": now,
                "status": "confirmed" if verified else "unconfirmed",
            }
            await self._save_submission_state()
            if not verified:
                raise UpdateFailed(
                    f"Portal nie potwierdził odczytu {reading:.3f} m³ dla wodomierza {meter_id}"
                )
        finally:
            await session.close()

    async def mark_all_notifications_read(self) -> int:
        session, base = await self._login_session()
        changed = 0
        try:
            for notification in (self.data or {}).get("notifications", []):
                if notification.get("read") or not notification.get("id"):
                    continue
                path = f"/wiadomosc/wiadomosc-odczytana/{notification['id']}"
                async with session.get(urljoin(base, path)) as response:
                    response.raise_for_status()
                changed += 1
            await self.async_request_refresh()
            _LOGGER.debug("eobywatel: oznaczono jako przeczytane %d wiadomości", changed)
            return changed
        finally:
            await session.close()

    async def set_subscription(self, category_id: str, enabled: bool) -> None:
        session, base = await self._login_session()
        try:
            async with session.get(urljoin(base, SUBSCRIPTIONS_PAGE)) as response:
                page = await response.text()
                response.raise_for_status()
            soup = BeautifulSoup(page, "html.parser")
            form = soup.find("form", action=SUBSCRIPTIONS_PAGE)
            if not form:
                form = soup.find("form", action=re.compile(r"/subskrypcja/new"))
            if not form:
                raise UpdateFailed("Nie znaleziono formularza subskrypcji")
            token_el = form.find("input", name=re.compile(r"\[_token\]$"))
            token = token_el.get("value") if token_el else None
            if not token:
                raise UpdateFailed("Nie znaleziono tokenu CSRF subskrypcji")
            selected = [
                str(inp.get("value"))
                for inp in form.select('input[type="checkbox"][name*="[kategorie]"]')
                if inp.has_attr("checked")
            ]
            category_id = str(category_id)
            if enabled and category_id not in selected:
                selected.append(category_id)
            if not enabled:
                selected = [value for value in selected if value != category_id]
            data = [
                ("hsi_platformapowiadomienbundle_nowasubskrypcja[kategorie][]", value)
                for value in selected
            ]
            data.append(("hsi_platformapowiadomienbundle_nowasubskrypcja[_token]", token))
            async with session.post(urljoin(base, SUBSCRIPTIONS_PAGE), data=data, allow_redirects=True) as response:
                response_text = await response.text()
                response.raise_for_status()
            verified = parse_subscriptions(response_text)
            enabled_ids = {c["id"] for c in verified.get("categories", []) if c.get("enabled")}
            if (category_id in selected) != (category_id in enabled_ids):
                raise UpdateFailed("Portal nie potwierdził zmiany subskrypcji")
            await self.async_request_refresh()
        finally:
            await session.close()


class EobywatelBaseEntity(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EobywatelCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "e-Obywatel (KKOnline)",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "KKOnline",
        }


class SimpleSensor(EobywatelBaseEntity):
    def __init__(self, coordinator, name, unique_id, icon, value_fn, attrs_fn=None):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._value_fn = value_fn
        self._attrs_fn = attrs_fn

    @property
    def native_value(self):
        return self._value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        return self._attrs_fn(self.coordinator.data or {}) if self._attrs_fn else None


class PointSensor(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:water"

    def __init__(self, coordinator, point, parent_device_id: str):
        super().__init__(coordinator)
        self.point_id = point["point_id"]
        self._attr_unique_id = f"{DOMAIN}_point_{self.point_id}_meters"
        self._attr_name = "Wodomierze"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"point_{self.point_id}")},
            "name": f"Punkt poboru {self.point_id}",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "Punkt poboru wody",
            "via_device_id": parent_device_id,
        }

    @property
    def native_value(self):
        return len([m for m in self.coordinator.data.get("meters", []) if m.get("point_id") == self.point_id])


class MeterSensor(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_device_class = "water"
    _attr_state_class = "total_increasing"
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:water"

    def __init__(self, coordinator, meter, parent_device_id: str):
        super().__init__(coordinator)
        self.meter_id = meter["meter_id"]
        
        self._attr_unique_id = f"{DOMAIN}_meter_{self.meter_id}_reading"
        self._attr_name = "Odczyt"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"meter_{self.meter_id}")},
            "name": f"Wodomierz {self.meter_id}",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "Wodomierz",
            "via_device_id": parent_device_id,
        }

    @property
    def meter(self):
        return next(
            (meter for meter in self.coordinator.data.get("meters", []) if meter.get("meter_id") == self.meter_id),
            None,
        )

    @property
    def native_value(self):
        return self.meter.get("reading_value") if self.meter else None

    @property
    def extra_state_attributes(self):
        meter = self.meter
        if not meter:
            return {}
        return {
            "point": meter.get("point"),
            "address": meter.get("address"),
            "point_id": meter.get("point_id"),
            "meter_id": meter.get("meter_id"),
            "history": meter.get("history", []),
            "portal_url": meter.get("url"),
        }


class MeterDateSensor(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = "date"
    _attr_icon = "mdi:calendar"

    def __init__(self, coordinator, meter, parent_device_id: str, date_type: str):
        super().__init__(coordinator)
        self.meter_id = meter["meter_id"]
        self.date_type = date_type
        
        self._attr_unique_id = f"{DOMAIN}_meter_{self.meter_id}_{self.date_type}"
        self._attr_name = "Data odczytu" if date_type == "reading_date" else "Data legalizacji"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"meter_{self.meter_id}")},
            "name": f"Wodomierz {self.meter_id}",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "Wodomierz",
            "via_device_id": parent_device_id,
        }

    @property
    def meter(self):
        return next(
            (meter for meter in self.coordinator.data.get("meters", []) if meter.get("meter_id") == self.meter_id),
            None,
        )

    @property
    def native_value(self):
        meter = self.meter
        if not meter:
            return None
        date_str = meter.get(self.date_type)
        if date_str and len(date_str) == 10 and date_str[2] == "." and date_str[5] == ".":
            # Convert DD.MM.YYYY to YYYY-MM-DD
            from datetime import date
            try:
                return date(int(date_str[6:10]), int(date_str[3:5]), int(date_str[0:2]))
            except ValueError:
                pass
        return None



class MeterLastSubmittedSensor(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:water-check"

    def __init__(self, coordinator, meter, parent_device_id: str):
        super().__init__(coordinator)
        self.meter_id = meter["meter_id"]
        self._attr_unique_id = f"{DOMAIN}_meter_{self.meter_id}_last_submitted"
        self._attr_name = "Ostatni wysłany stan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"meter_{self.meter_id}")},
            "name": f"Wodomierz {self.meter_id}",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "Wodomierz",
            "via_device_id": parent_device_id,
        }

    @property
    def native_value(self):
        state = self.coordinator.submission_state.get(self.meter_id, {})
        return state.get("reading")

    @property
    def extra_state_attributes(self):
        state = self.coordinator.submission_state.get(self.meter_id, {})
        return {"status": state.get("status", "brak"), "submitted_at": state.get("timestamp")}


class MeterLastSubmittedDateSensor(CoordinatorEntity[EobywatelCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = "timestamp"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, coordinator, meter, parent_device_id: str):
        super().__init__(coordinator)
        self.meter_id = meter["meter_id"]
        self._attr_unique_id = f"{DOMAIN}_meter_{self.meter_id}_last_submitted_at"
        self._attr_name = "Data ostatniego wysłania"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"meter_{self.meter_id}")},
            "name": f"Wodomierz {self.meter_id}",
            "manufacturer": "e-Obywatel / Portal Mieszkańca",
            "model": "Wodomierz",
            "via_device_id": parent_device_id,
        }

    @property
    def native_value(self):
        value = self.coordinator.submission_state.get(self.meter_id, {}).get("timestamp")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


class NearestMessageDateSensor(EobywatelBaseEntity):
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_nearest_message_date"
        self._attr_name = "Najbliższy termin z wiadomości"

    @property
    def native_value(self):
        messages = (self.coordinator.data or {}).get("notifications", [])
        dates = []
        for msg in messages:
            for match in re.findall(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(20\d{2})\b", msg.get("body", "")):
                try:
                    from datetime import date
                    d = date(int(match[2]), int(match[1]), int(match[0]))
                    dates.append((d, msg.get("title")))
                except ValueError:
                    pass
        if not dates:
            return None
        return min(dates)[0].isoformat()

    @property
    def extra_state_attributes(self):
        messages = (self.coordinator.data or {}).get("notifications", [])
        return {"source_messages": [m.get("title") for m in messages]}


class LatestMessageSensor(EobywatelBaseEntity):
    _attr_icon = "mdi:email-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_latest_message"
        self._attr_name = "Ostatnia wiadomość"

    @property
    def native_value(self):
        messages = (self.coordinator.data or {}).get("notifications", [])
        return messages[0].get("title") if messages else "Brak wiadomości"

    @property
    def extra_state_attributes(self):
        messages = (self.coordinator.data or {}).get("notifications", [])
        if not messages:
            return {"count": 0}
        latest = messages[0]
        return {
            "id": latest.get("id"),
            "title": latest.get("title"),
            "body": latest.get("body"),
            "short": latest.get("short"),
            "read": latest.get("read"),
            "timestamp": latest.get("timestamp"),
            "channels": latest.get("channels", []),
            "count": len(messages),
        }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EobywatelCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    
    from homeassistant.helpers import device_registry as dr
    device_registry = dr.async_get(hass)
    main_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="e-Obywatel (KKOnline)",
        manufacturer="e-Obywatel / Portal Mieszkańca",
        model="KKOnline",
    )
    main_device_id = main_device.id

    entities: list[SensorEntity] = [
        SimpleSensor(coordinator, "Status", f"{DOMAIN}_status", "mdi:account-check", lambda x: x.get("status", "offline")),
        SimpleSensor(coordinator, "Punkty poboru", f"{DOMAIN}_points", "mdi:map-marker-multiple", lambda x: len(x.get("points", []))),
        SimpleSensor(coordinator, "Wodomierze", f"{DOMAIN}_meters", "mdi:water", lambda x: len(x.get("meters", []))),
        SimpleSensor(coordinator, "Wiadomości", f"{DOMAIN}_messages", "mdi:email-multiple", lambda x: len(x.get("notifications", []))),
        SimpleSensor(
            coordinator,
            "Nieprzeczytane wiadomości",
            f"{DOMAIN}_unread_messages",
            "mdi:email-alert",
            lambda x: sum(not n.get("read") for n in x.get("notifications", [])),
        ),
        SimpleSensor(
            coordinator,
            "Faktury",
            f"{DOMAIN}_invoices",
            "mdi:receipt-text-outline",
            lambda x: (
                x.get("invoices", {}).get("count", 0)
                if x.get("invoices", {}).get("status") == "available"
                else x.get("invoices", {}).get("status", "unknown")
            ),
            lambda x: {
                key: value
                for key, value in x.get("invoices", {}).items()
                if key != "items"
            }
            | {
                "items": x.get("invoices", {}).get("items", [])[:10],
            },
        ),
        NearestMessageDateSensor(coordinator),
        LatestMessageSensor(coordinator),
        SimpleSensor(
            coordinator,
            "Subskrypcje",
            f"{DOMAIN}_subscriptions",
            "mdi:bell-check",
            lambda x: len(x.get("subscriptions", {}).get("enabled", [])),
            lambda x: {
                "enabled": x.get("subscriptions", {}).get("enabled", []),
                "categories": x.get("subscriptions", {}).get("categories", []),
            },
        ),
    ]

    for point in data.get("points", []):
        if not point.get("point_id"):
            continue
        point_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"point_{point['point_id']}")},
            name=f"Punkt poboru {point['point_id']}",
            manufacturer="e-Obywatel / Portal Mieszkańca",
            model="Punkt poboru wody",
            via_device_id=main_device_id,
        )
        entities.append(PointSensor(coordinator, point, main_device_id))

    for meter in data.get("meters", []):
        point_id = meter.get("point_id")
        parent_id = main_device_id
        if point_id:
            point_device = device_registry.async_get_device_by_identifier((DOMAIN, f"point_{point_id}"), entry.entry_id)
            if point_device:
                parent_id = point_device.id
        entities.append(MeterSensor(coordinator, meter, parent_id))
        entities.append(MeterDateSensor(coordinator, meter, parent_id, "reading_date"))
        entities.append(MeterDateSensor(coordinator, meter, parent_id, "legalization_date"))
        entities.append(MeterLastSubmittedSensor(coordinator, meter, parent_id))
        entities.append(MeterLastSubmittedDateSensor(coordinator, meter, parent_id))

    async_add_entities(entities)