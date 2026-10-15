"""WHOOP API v2: вход через OAuth 2.0 и синхронизация данных в store.

Документация: https://developer.whoop.com/api, https://developer.whoop.com/docs/developing/oauth
- вход: AUTH_URL?response_type=code&client_id&redirect_uri&scope&state (state — не короче 8 символов);
- токен живёт expires_in секунд; refresh_token выдают только со scope «offline», при обновлении
  старый refresh_token перестаёт работать — сохраняем новый сразу;
- списки отдают по 25 записей (limit) и next_token для следующей страницы.
"""
import datetime
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API = "https://api.prod.whoop.com/developer"
SCOPES = "offline read:recovery read:cycles read:workout read:sleep read:profile read:body_measurement"
PATHS = {
    "cycle": "/v2/cycle",
    "recovery": "/v2/recovery",
    "sleep": "/v2/activity/sleep",
    "workout": "/v2/activity/workout",
}
_locks = {}
# WHOOP за Cloudflare: «Python-urllib/3.x» он режет (403, error 1010 «browser's signature»). Шлём обычные заголовки
# браузера; если Cloudflare всё равно отказал — тот же запрос через curl (у него другой «отпечаток»).
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/18.0 Mobile/15E148 Safari/604.1 FormaCoach/1.0")
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "ru,en;q=0.8"}


class HTTPFail(Exception):
    """Ответ WHOOP с ошибкой: code — HTTP-код, body — начало тела, headers — заголовки (Retry-After)."""
    def __init__(self, code, body="", headers=None):
        super().__init__(f"WHOOP {code}: {body[:200]}")
        self.code, self.body, self.headers = code, body, headers or {}


def blocked(code, body):
    """Отказ Cloudflare по «отпечатку» клиента, а не ответ самого WHOOP."""
    b = (body or "").lower()
    return code in (403, 1010) and ("1010" in b or "cloudflare" in b or "browser's signature" in b)


def _curl(method, url, headers, data, timeout):
    if not shutil.which("curl"):
        raise HTTPFail(403, "Cloudflare: нет curl для обхода")
    cmd = ["curl", "-sS", "-X", method, "--max-time", str(timeout), "-w", "\n%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    try:
        r = subprocess.run(cmd, input=data or b"", capture_output=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise HTTPFail(504, "WHOOP не ответил вовремя") from None
    out = r.stdout.decode(errors="replace")
    body, _, code = out.rpartition("\n")
    if r.returncode or not code.isdigit():
        raise HTTPFail(502, (r.stderr.decode(errors="replace") or out)[:200])
    if int(code) >= 400:
        raise HTTPFail(int(code), body)
    return json.loads(body or "null")


def request(method, url, headers=None, data=None, timeout=30):
    """HTTP к WHOOP с нормальными заголовками; ответ — JSON. Ошибка — HTTPFail; отказ Cloudflare — повтор через curl."""
    h = {**HEADERS, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if not blocked(e.code, body):
            raise HTTPFail(e.code, body, e.headers) from None
        print("whoop: Cloudflare отказал urllib, повторяю через curl")
    return _curl(method, url, h, data, timeout)


class NotConnected(Exception):
    """Нет токена или WHOOP его отозвал — нужно войти заново."""


def auth_link(client_id, redirect, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "scope": SCOPES, "state": state,
    })


def _post_form(url, form):
    try:
        return request("POST", url, {"Content-Type": "application/x-www-form-urlencoded"},
                       urllib.parse.urlencode(form).encode())
    except HTTPFail as e:
        raise RuntimeError(f"WHOOP {e.code}: {e.body[:300]}") from None


def exchange(db, uid, code, client_id, secret, redirect):
    t = _post_form(TOKEN_URL, {"grant_type": "authorization_code", "code": code, "client_id": client_id,
                               "client_secret": secret, "redirect_uri": redirect})
    db.save_token(uid, t["access_token"], t.get("refresh_token"), time.time() + int(t.get("expires_in") or 3600))
    return t


def _refresh(db, uid, client_id, secret):
    tok = db.token(uid)
    if not tok or not tok.get("refresh"):
        raise NotConnected("нет refresh_token")
    try:
        t = _post_form(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tok["refresh"],
                                   "client_id": client_id, "client_secret": secret, "scope": "offline"})
    except RuntimeError as e:
        if any(c in str(e) for c in ("400", "401")):
            raise NotConnected(str(e)) from None
        raise
    db.save_token(uid, t["access_token"], t.get("refresh_token") or tok["refresh"],
                  time.time() + int(t.get("expires_in") or 3600))


class Client:
    def __init__(self, db, uid, client_id, secret):
        self.db, self.uid, self.cid, self.secret = db, uid, client_id, secret

    def _access(self, force=False):
        # одно обновление токена за раз: второе одновременное WHOOP отклонит
        with _locks.setdefault(self.uid, threading.Lock()):
            tok = self.db.token(self.uid)
            if not tok:
                raise NotConnected("WHOOP не подключён")
            if force or (tok.get("expires") or 0) - time.time() < 120:
                _refresh(self.db, self.uid, self.cid, self.secret)
                tok = self.db.token(self.uid)
            return tok["access"]

    def get(self, path, params=None):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(3):
            try:
                return request("GET", url, {"Authorization": "Bearer " + self._access(force=attempt == 1)})
            except HTTPFail as e:
                if e.code == 401 and attempt == 0:
                    continue  # токен протух раньше срока — обновим и повторим
                if e.code == 429 and attempt < 2:
                    try:
                        wait = int((e.headers or {}).get("Retry-After") or 5)
                    except (TypeError, ValueError):
                        wait = 5
                    time.sleep(min(wait, 60))
                    continue
                if e.code == 404:
                    return None
                raise RuntimeError(f"WHOOP {e.code}: {e.body[:200]}") from None
        raise NotConnected("WHOOP не принимает токен")

    def collection(self, kind, start, limit_pages=40):
        out, token = [], None
        for _ in range(limit_pages):
            params = {"limit": 25, "start": start}
            if token:
                params["nextToken"] = token
            page = self.get(PATHS[kind], params) or {}
            out += page.get("records") or []
            token = page.get("next_token")
            if not token:
                break
        return out


# ---------- день записи ----------

def _parse(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _local(ts, offset):
    t = _parse(ts)
    if not t:
        return None
    try:
        sign = -1 if offset.startswith("-") else 1
        h, m = offset.lstrip("+-").split(":")
        return t + sign * datetime.timedelta(hours=int(h), minutes=int(m))
    except (AttributeError, ValueError):
        return t + datetime.timedelta(hours=3)  # по умолчанию Москва


def day_of(kind, r, cycles=None):
    """Календарный день записи у пользователя. Цикл WHOOP начинается со сна накануне или с утра —
    сдвиг +6 часов относит его к дню, который он покрывает. Сон — к дню пробуждения."""
    off = r.get("timezone_offset") or "+03:00"
    if kind == "cycle":
        t = _local(r.get("start"), off)
        return (t + datetime.timedelta(hours=6)).date().isoformat() if t else None
    if kind == "sleep":
        t = _local(r.get("end") or r.get("start"), off)
        if t and not r.get("end"):
            t += datetime.timedelta(hours=6)
        return t.date().isoformat() if t else None
    if kind == "workout":
        t = _local(r.get("start"), off)
        return t.date().isoformat() if t else None
    if kind == "recovery":
        c = (cycles or {}).get(str(r.get("cycle_id")))
        if c:
            return day_of("cycle", c)
        t = _local(r.get("created_at"), off)
        return t.date().isoformat() if t else None
    return None


def rid(kind, r):
    return str(r.get("cycle_id") if kind == "recovery" else r.get("id"))


def sync(db, uid, client_id, secret, days=3):
    """Подтягивает записи за последние days дней. Возвращает новые оценённые записи [(kind, запись)] —
    по ним бот присылает разбор утра и тренировки."""
    cl = Client(db, uid, client_id, secret)
    start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    old = {k: {rid(k, r): r.get("score_state") for r in db.records(uid, k, (datetime.date.today()
           - datetime.timedelta(days=days + 2)).isoformat())} for k in PATHS}
    fresh = []
    cycles = {str(c.get("id")): c for c in db.records(uid, "cycle")[-90:]}
    for kind in ("cycle", "sleep", "workout", "recovery"):
        for r in cl.collection(kind, start):
            if kind == "cycle":
                cycles[str(r.get("id"))] = r
            day = day_of(kind, r, cycles)
            key = rid(kind, r)
            db.put(uid, kind, key, day, r.get("start") or r.get("created_at") or "", r)
            if r.get("score_state") == "SCORED" and old[kind].get(key) != "SCORED":
                fresh.append((kind, dict(r, _day=day)))
    if days > 7 or not db.meta(f"body:{uid}") or time.time() - float(db.meta(f"body_at:{uid}") or 0) > 86400:
        try:
            body = cl.get("/v2/user/measurement/body") or {}
            prof = cl.get("/v2/user/profile/basic") or {}
            db.meta(f"body:{uid}", json.dumps({**body, "first_name": prof.get("first_name")}, ensure_ascii=False))
            db.meta(f"body_at:{uid}", time.time())
        except RuntimeError as e:
            print("whoop body:", e)
    db.mark_synced(uid)
    return fresh


def body(db, uid):
    try:
        return json.loads(db.meta(f"body:{uid}") or "{}")
    except ValueError:
        return {}
