#!/usr/bin/env python3
"""Read-only HondaLink probe for Clarity Plug-In Hybrid (TelematicsPlatform 2ZSPHEV).

Replays what the HondaLink Android app (5.5.3) does to show a Clarity's
dashboard, as recovered from the decompiled APK:

1. Sign in (HIDAS), exactly as the integration does.
2. CIG dashboard with the EV body the app sends for an enrolled Clarity:
       POST https://wsc.hondaweb.com/REST/NGT/CIG/dbd/latest/{VIN}
       {"evInfoRequest": {"reqType": "all_info", "setUnit": {"dist": "mile", "temp": "F"}}}
   The reply's responseBody.evStatus.vehicleInfo carries battery %, ranges,
   charge/plug/climate status, temperatures and location.
3. The older "GTC" backend handshake and dashboard (needed later for remote
   climate/charge commands, which go through it):
       GET  https://www.jp.hondalink.com/api/VinServiceAuth      (sets a session cookie)
       POST https://www.jp.hondalink.com/api/GW/VinService/GetLatestVehicleData

It sends no remote commands and does not wake the vehicle. Location
coordinates and the TCU's IMSI are never printed. Tokens are not printed and
VINs are shortened to their first 8 characters, but read the output before
sharing it.

Usage:  python3 hondalink_clarity_probe.py [--vin VIN]
Credentials come from HONDALINK_EMAIL / HONDALINK_PASSWORD, or are prompted for.
Needs only the Python 3.9+ standard library.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

TIMEOUT = 45

# Values from the integration's const.py (unchanged in app 5.5.3).
CLIENT_ID = "HondaLinkAndroidApp0074"
CLIENT_SECRET = "rETFrZcLyUycsSblksCP"
DEVICE_DESCRIPTION = "Android"
APP_USER_AGENT = "HondaLink/5.0.51 (Android)"
IDENTITY_BASE = "https://identity.services.honda.com"
API_BASE = "https://wsc.hondaweb.com"
GTC_BASE = "https://www.jp.hondalink.com"
HONDALINK_BUSINESS_ID = "HONDALINK CONNECT"
HONDALINK_SYSTEM_ID = "com.honda.hondalink.cv_android"

# From the APK (com.honda.common.utils.j0.h4 and ij.p interceptors).
GTC_SERVICE_KEY_SALT = "DL2mBuVQsT7d54c2xaDf94jYe8D35c2p"
GTC_AUTH_APP_ID = "hondalink.android"
GTC_APP_ID = "com.honda.hondalink.connect"
GTC_APP_VERSION = "5.5.3"

CLARITY_DASHBOARD_BODY = {"evInfoRequest": {"reqType": "all_info", "setUnit": {"dist": "mile", "temp": "F"}}}

# Never print these vehicleInfo fields' values.
SENSITIVE_FIELDS = {"curPosLat", "curPosLon", "naviCurPosLat", "naviCurPosLon", "IMSI", "imsi"}


def mask_vin(vin: str) -> str:
    vin = str(vin or "")
    return vin[:8] + "*" * max(len(vin) - 8, 0)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def gtc_service_key() -> str:
    """SHA-256 hex of salt + today's date (GMT, yyyyMMdd), as j0.h4() computes it."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return hashlib.sha256((GTC_SERVICE_KEY_SALT + day).encode()).hexdigest()


def error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    body = payload.get("responseBody") if isinstance(payload.get("responseBody"), dict) else {}
    header = payload.get("Header") if isinstance(payload.get("Header"), dict) else {}
    for value in (payload.get("errorCode"), payload.get("ErrorCode"), body.get("errorCode"), header.get("ErrorCode")):
        if value not in (None, ""):
            return str(value)
    return None


def error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    body = payload.get("responseBody") if isinstance(payload.get("responseBody"), dict) else {}
    header = payload.get("Header") if isinstance(payload.get("Header"), dict) else {}
    for value in (payload.get("errorMessage"), payload.get("message"), payload.get("msg"),
                  body.get("errorMessage"), header.get("ErrorDescription")):
        if value:
            text = str(value)
            return text if len(text) <= 240 else text[:237] + "..."
    return None


class Client:
    def __init__(self) -> None:
        self.client_reg_key = ""
        self.access_token = ""
        self.country = "US"
        self.language = "en"
        self.hidas_ident = ""
        self.device_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())
        self.gtc_cookies: dict[str, str] = {}

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                status, raw, resp_headers = response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            status, raw, resp_headers = err.code, err.read(), err.headers
        text = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            payload = {"_non_json_body_length": len(text)}
        return status, payload, resp_headers

    # --- HIDAS sign-in (same as the integration) ---------------------------
    def identity(self, path: str, form: dict[str, str]):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": APP_USER_AGENT,
        }
        return self.send("POST", f"{IDENTITY_BASE}{path}", headers, urllib.parse.urlencode(form).encode())[:2]

    def login(self, email: str, password: str) -> None:
        status, data = self.identity("/hidas/rs/client/register", {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
        try:
            self.client_reg_key = data["clientregistrationkey"]["client_reg_key"]
        except (KeyError, TypeError):
            sys.exit(f"Client registration failed: HTTP {status} errorCode={error_code(data)}")
        status, data = self.identity(
            "/hidas/rs/token/generate",
            {"username": email, "password": password, "description": DEVICE_DESCRIPTION,
             "client_reg_key": self.client_reg_key},
        )
        token = (data or {}).get("token") or {}
        if data.get("request_status") != "success" or not token.get("access_token"):
            sys.exit(f"Login failed: HTTP {status} errorCode={error_code(data)} {error_message(data) or ''}")
        user = data.get("user") or {}
        self.access_token = token["access_token"]
        self.country = user.get("country_code") or self.country
        self.language = user.get("language_code") or self.language
        self.hidas_ident = user.get("hidas_ident") or ""

    # --- CIG / NGT (wsc.hondaweb.com) ---------------------------------------
    def ngt(self, method: str, path: str, json_body: dict[str, Any] | None = None):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "bearerToken": self.access_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": APP_USER_AGENT,
            "hondaHeaderType.version": "1.0",
            "hondaHeaderType.messageId": str(uuid.uuid4()),
            "hondaHeaderType.siteId": self.client_reg_key,
            "hondaHeaderType.businessId": HONDALINK_BUSINESS_ID,
            "hondaHeaderType.systemId": HONDALINK_SYSTEM_ID,
            "hondaHeaderType.collectedTimestamp": iso_now(),
            "hondaHeaderType.clientType": "Mobile",
            "hondaHeaderType.deviceID": self.device_id,
            "hondaHeaderType.sessionID": self.session_id,
            "hondaHeaderType.country_code": self.country,
            "hondaHeaderType.language_code": self.language,
        }
        if self.hidas_ident:
            headers["hondaHeaderType.userId"] = self.hidas_ident
            headers["hondaHeaderType.hidasId"] = self.hidas_ident
        body = json.dumps(json_body).encode() if json_body is not None else None
        return self.send(method, f"{API_BASE}{path}", headers, body)[:2]

    # --- GTC (www.jp.hondalink.com) -----------------------------------------
    def _cookie_header(self) -> dict[str, str]:
        if not self.gtc_cookies:
            return {}
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.gtc_cookies.items())}

    def _store_cookies(self, headers) -> list[str]:
        names = []
        for raw in headers.get_all("Set-Cookie") or []:
            pair = raw.split(";", 1)[0]
            if "=" in pair:
                name, value = pair.split("=", 1)
                self.gtc_cookies[name.strip()] = value.strip()
                names.append(name.strip())
        return names

    def gtc_service_auth(self):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": APP_USER_AGENT,
            "X-Service-Key": gtc_service_key(),
            "X-App-Id": GTC_AUTH_APP_ID,
            "X-App-Version": "1.0",
            "X-Device-Id": self.device_id,
            **self._cookie_header(),
        }
        status, payload, resp_headers = self.send("GET", f"{GTC_BASE}/api/VinServiceAuth", headers, None)
        return status, payload, self._store_cookies(resp_headers)

    def gtc_latest_vehicle_data(self, vin: str):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": APP_USER_AGENT,
            "X-App-Id": GTC_APP_ID,
            "X-App-Version": GTC_APP_VERSION,
            "X-Device-Id": self.device_id,
            "X-Vin-Auth-Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "X-Vin": vin,
            "BearerToken": self.access_token,
            **self._cookie_header(),
        }
        form = urllib.parse.urlencode({"startTime": "", "endTime": "", "type": "ALL"}).encode()
        status, payload, resp_headers = self.send(
            "POST", f"{GTC_BASE}/api/GW/VinService/GetLatestVehicleData", headers, form
        )
        self._store_cookies(resp_headers)
        return status, payload


def report(label: str, status: int, payload: Any) -> None:
    line = f"    {label}: HTTP {status}"
    if isinstance(payload, dict):
        for key in ("status", "msg", "apiName", "count"):
            if payload.get(key) not in (None, ""):
                line += f", {key}={payload.get(key)!r}"
    code = error_code(payload)
    if code:
        line += f", errorCode={code}"
    print(line)
    message = error_message(payload)
    if message and (code or status >= 400):
        print(f"      message: {message}")


def describe_value(value: Any) -> str:
    if isinstance(value, dict):
        if "value" in value or "valid" in value:
            text = repr(value.get("value"))
            if value.get("unit"):
                text += f" {value.get('unit')}"
            if value.get("valid") is not None:
                text += f" (valid={value.get('valid')})"
            return text
        return "{" + ", ".join(sorted(value.keys())) + "}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    return repr(value)


def print_ev_vehicle_info(body: dict[str, Any]) -> None:
    ev_status = body.get("evStatus") if isinstance(body.get("evStatus"), dict) else None
    print(f"      responseBody keys: {sorted(body.keys())}")
    if not ev_status:
        print("      evStatus: (absent)")
        return
    print(f"      evStatus keys: {sorted(ev_status.keys())}")
    info = ev_status.get("vehicleInfo")
    if not isinstance(info, dict):
        print("      evStatus.vehicleInfo: (absent)")
        return
    print("      evStatus.vehicleInfo:")
    for key in sorted(info):
        if key in SENSITIVE_FIELDS:
            present = info[key] not in (None, "", {}) and not (isinstance(info[key], dict) and not info[key].get("value"))
            print(f"        {key}: {'(present, not shown)' if present else '(empty)'}")
        else:
            print(f"        {key}: {describe_value(info[key])}")


def print_gtc_dashboard(payload: Any) -> None:
    results = payload.get("resultList") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        print("      resultList: (empty)")
        return
    for item in results[:1]:
        dashboard = (item or {}).get("dashboard") or {}
        print(f"      cachedDate: {item.get('cachedDate')!r}")
        print(f"      dashboard keys: {sorted(dashboard.keys())}")
        for section in ("vehicleInfo", "tripLog"):
            data = dashboard.get(section)
            if isinstance(data, dict):
                fields = ", ".join(f"{k}={describe_value(v)}" for k, v in sorted(data.items()))
                print(f"      {section}: {fields}")


def probe_vehicle(client: Client, vehicle: dict[str, Any]) -> None:
    vin = str(vehicle.get("VIN") or "")
    print(f"\nVehicle {mask_vin(vin)}: {vehicle.get('ModelYear')} {vehicle.get('ModelGroupNameFriendly')}, "
          f"TelematicsPlatform={vehicle.get('TelematicsPlatform')!r}")

    print("  1. CIG dashboard with the app's Clarity body:")
    status, data = client.ngt("POST", f"/REST/NGT/CIG/dbd/latest/{vin}", CLARITY_DASHBOARD_BODY)
    report("evInfoRequest body", status, data)
    body = data.get("responseBody") if isinstance(data, dict) else None
    if status == 200 and isinstance(body, dict):
        print_ev_vehicle_info(body)
    elif error_code(data) == "0001-01-1151":
        with_dates = {**CLARITY_DASHBOARD_BODY, "fromDate": "", "toDate": ""}
        status, data = client.ngt("POST", f"/REST/NGT/CIG/dbd/latest/{vin}", with_dates)
        report("evInfoRequest + fromDate/toDate", status, data)
        body = data.get("responseBody") if isinstance(data, dict) else None
        if status == 200 and isinstance(body, dict):
            print_ev_vehicle_info(body)

    print("  2. Older (GTC) backend:")
    status, data, cookies = client.gtc_service_auth()
    report("VinServiceAuth", status, data)
    print(f"      cookies set: {cookies or 'none'}")
    status, data = client.gtc_latest_vehicle_data(vin)
    report("GetLatestVehicleData", status, data)
    if status == 200:
        print_gtc_dashboard(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vin", help="only probe this VIN")
    args = parser.parse_args()

    email = os.environ.get("HONDALINK_EMAIL") or input("HondaLink email: ").strip()
    password = os.environ.get("HONDALINK_PASSWORD") or getpass.getpass("HondaLink password: ")

    client = Client()
    client.login(email, password)
    print("Login: OK")

    status, data = client.ngt("GET", "/REST/NGT/MyVehicle/1.0")
    vehicles = (data or {}).get("vehicleInfo") if isinstance(data, dict) else None
    if status != 200 or not isinstance(vehicles, list):
        report("MyVehicle", status, data)
        sys.exit(1)
    if args.vin:
        vehicles = [v for v in vehicles if str(v.get("VIN", "")).upper() == args.vin.strip().upper()]
    print(f"Vehicles on account: {len(vehicles)}")
    for vehicle in vehicles:
        if isinstance(vehicle, dict):
            probe_vehicle(client, vehicle)


if __name__ == "__main__":
    main()
