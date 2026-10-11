"""Клиент customer-api.domyland.ru (приложение «Домиленд+»): вход через Яндекс и разовые пропуска.

Откуда что известно:
- вход, заголовки, места — открытая интеграция github.com/he110/ha-domyland (работает);
- пропуска — из приложения «Домиленд+» 4.25.5 (Retrofit-интерфейсы):
    GET  v2/pass?fromRow=0                 мои пропуска (карточки со статусом и кнопками)
    POST v2/pass                           создать разовый пропуск
    POST v2/pass/{id}/cancel               отменить
    GET  /pass/history?fromRow=0&...       прошлые разовые пропуска (passTypeId, passSubTypeId, regNumber…)
    GET|PUT /auto-pass/places              настройка «Автопропуск» по адресам
  Тело POST v2/pass: {"type", "subType", "passes": [{"entityId", "regNumber"}], "arriveDate" (мс), "comment"}.
  Такси в приложении: type=1 (транспорт), subType=5. Если в истории есть прошлый пропуск такси,
  бот берёт числа оттуда (calibrate).

Только стандартная библиотека.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://customer-api.domyland.ru"
# Яндекс OAuth client_id приложения «Домиленд+» (из ha-domyland). Секрет не нужен: implicit-вход.
YANDEX_CLIENT_ID = "e9f2a97b605c4ad8aad0a3b3e68658af"
LOGIN_URL = f"https://oauth.yandex.ru/authorize?response_type=token&client_id={YANDEX_CLIENT_ID}"

TAXI_TYPE, TAXI_SUBTYPE = 1, 5

HEADERS = {
    "AppName": "superdom-android",
    "OriginalAppName": "superdom-android",
    "AppVersion": "4.23.1",
    "AppTheme": "light",
    "TimeZone": "Europe/Moscow",
    "Accept-Language": "ru-RU",
    "User-Agent": "okhttp/5.3.2",
}


class Error(Exception):
    pass


class AuthError(Error):
    """Токен не подходит или истёк — нужно войти заново."""


# ---------- токен Яндекса ----------

_TOKEN_RE = re.compile(r"(y[0-3]_[A-Za-z0-9_-]{20,})")


def extract_token(raw):
    """Токен из того, что прислал человек: сам y0_… или вся ссылка с #access_token=…"""
    raw = (raw or "").strip()
    if "access_token=" in raw:
        part = raw.split("#", 1)[-1] if "#" in raw else urllib.parse.urlparse(raw).query
        for k, v in urllib.parse.parse_qsl(part.split("?", 1)[-1]):
            if k == "access_token" and v:
                return v
    m = _TOKEN_RE.search(raw)
    return m.group(1) if m else None


# ---------- госномер ----------

LAT2CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")
_L = "АВЕКМНОРСТУХ"
PLATE_RES = [
    re.compile(rf"[{_L}]\d{{3}}[{_L}]{{2}}\d{{2,3}}"),   # А123ВС777 — обычный
    re.compile(rf"[{_L}]{{2}}\d{{3}}\d{{2,3}}"),         # АВ12377 — жёлтый номер такси
]


def plates(text):
    """Все госномера РФ в тексте (латиница-двойники → кириллица, без пробелов и дефисов)."""
    s = (text or "").upper().replace("Ё", "Е").translate(LAT2CYR)
    s = re.sub(r"(?<=[\dА-Я])[\s\-|·.]+(?=[\dА-Я])", "", s)  # «А 123 ВС 777», «А123ВС|777»
    found = []
    for rx in PLATE_RES:
        for m in rx.finditer(s):
            if m.group(0) not in found and not any(m.group(0) in f for f in found):
                found.append(m.group(0))
    return found


def pretty(plate):
    """А123ВС777 → «А 123 ВС 777», АВ12377 → «АВ 123 77»."""
    m = re.fullmatch(r"([А-Я])(\d{3})([А-Я]{2})(\d{2,3})", plate)
    if m:
        return " ".join(m.groups())
    m = re.fullmatch(r"([А-Я]{2})(\d{3})(\d{2,3})", plate)
    if m:
        return " ".join(m.groups())
    return plate


# ---------- API ----------

class Client:
    def __init__(self, yandex_token, jwt=None, place_id=None, building_id=None, timeout=25):
        self.yandex_token = yandex_token
        self.jwt = jwt
        self.place_id = place_id
        self.building_id = building_id
        self.timeout = timeout

    def request(self, method, path, body=None, query=None, auth=True, place=True):
        h = dict(HEADERS, **{"x-yandex-oauth": self.yandex_token})
        if auth:
            if not self.jwt:
                raise AuthError("нет JWT")
            h["Authorization"] = self.jwt
        if place == "empty":  # список мест просят без выбранного дома
            h["placeId"] = h["buildingId"] = ""
        elif place:
            h["placeId"] = str(self.place_id or "")
            h["buildingId"] = str(self.building_id or "")
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            h["Content-Type"] = "application/json; charset=utf-8"
        url = API + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise AuthError(f"{e.code}: {text}")
            raise Error(f"{method} {path} → HTTP {e.code}: {text}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Error(f"Сеть: {e}")
        try:
            res = json.loads(text) if text.strip() else {}
        except ValueError:
            raise Error(f"Не JSON в ответе {path}: {text[:200]}")
        if isinstance(res, dict) and res.get("success") is False:
            raise Error(api_message(res) or f"{path}: success=false")
        return res.get("data") if isinstance(res, dict) and "data" in res else res

    # --- вход ---

    def login(self):
        """Яндекс-токен → JWT Домиленда. Любой отказ здесь — проблема токена."""
        body = {"deviceModel": "KPP bot", "deviceOSName": "Linux", "deviceOSVersion": "1",
                "deviceVendor": "KPP", "token": self.yandex_token}
        try:
            data = self.request("POST", "/auth/yandex/scenario", body, auth=False, place=False)
            self.jwt = data["payload"]["authData"]["token"]
        except AuthError:
            raise
        except (Error, KeyError, TypeError) as e:
            raise AuthError(str(e))
        return self.jwt

    def me(self):
        return self.request("GET", "/current-customer", place=False) or {}

    def places(self):
        data = self.request("GET", "/current-customer/places", place="empty")
        return (data or {}).get("places") or []

    # --- пропуска ---

    def create_taxi_pass(self, plate, type_id=TAXI_TYPE, subtype_id=TAXI_SUBTYPE, when=None, comment=""):
        body = {
            "type": type_id,
            "subType": subtype_id,
            "passes": [{"entityId": str(uuid.uuid4()), "regNumber": plate}],
            "arriveDate": int((when or time.time()) * 1000),
            "comment": comment,
        }
        return self.request("POST", "v2/pass", body) or {}

    def passes(self, from_row=0):
        return (self.request("GET", "v2/pass", query={"fromRow": from_row}) or {}).get("items") or []

    def cancel(self, pass_id):
        return self.request("POST", f"v2/pass/{pass_id}/cancel", {})

    def history(self, from_row=0):
        q = {"fromRow": from_row, "searchParam": "", "subtype": ""}
        return (self.request("GET", "/pass/history", query=q) or {}).get("items") or []

    def calibrate(self):
        """Числа type/subType такси из прошлого пропуска, если такой есть. Иначе — из приложения (1, 5)."""
        for p in self.history():
            if p.get("passSubTypeId") == TAXI_SUBTYPE or "такси" in str(p.get("title", "")).lower():
                return p.get("passTypeId") or TAXI_TYPE, p.get("passSubTypeId") or TAXI_SUBTYPE
        return TAXI_TYPE, TAXI_SUBTYPE

    # --- автопропуск ---

    def autopass(self):
        return self.request("GET", "/auto-pass/places") or {}

    def set_autopass(self, places):
        return self.request("PUT", "/auto-pass/places", {"places": places}) or {}


def api_message(res):
    """Человеческий текст ошибки из ответа API, если он есть."""
    if not isinstance(res, dict):
        return ""
    for k in ("message", "error", "errorMessage", "title"):
        v = res.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            for kk in ("message", "title", "description"):
                if isinstance(v.get(kk), str):
                    return v[kk]
    return ""


def created_id(resp):
    """id созданного пропуска из ответа v2/pass (items[].id), если есть."""
    for it in (resp or {}).get("items") or []:
        if isinstance(it, dict) and it.get("id") is not None:
            return it["id"]
    return None
