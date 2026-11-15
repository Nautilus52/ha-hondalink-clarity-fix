#!/usr/bin/env python3
"""Read-only HondaLink platform probe (see issue #4).

Signs in the same way the integration does, then for each vehicle on the account
prints the fields that decide which Honda telematics backend serves it, and how
the CIG dashboard endpoint responds. It sends no remote commands and does not
wake the vehicle.

Usage (from the repository folder, or as a standalone download):

    python3 tools/hondalink_platform_probe.py [--vin VIN]

Credentials are read from HONDALINK_EMAIL / HONDALINK_PASSWORD, or prompted for.
They are never written to disk. Tokens are not printed and VINs are shortened to
their first 8 characters (model information only), but read the output before
you share it.

Needs only the Python 3.9+ standard library.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIMEOUT = 30

VEHICLE_FIELDS = (
    "ModelYear",
    "ModelGroupNameFriendly",
    "ModelCode",
    "DivisionName",
    "CountryCoded",
    "TelematicsPlatform",
    "TelematicsFlag",
    "TelematicsUnit",
    "Enrollment",
    "EnrollmentMethod",
    "HeadUnit",
    "EmbeddedNavi",
)

DASHBOARD_BODIES = (
    ("integration body", lambda vin: {"fromDate": "", "toDate": ""}),
    ("empty body", lambda vin: {}),
    ("device body", lambda vin: {"device": vin}),
)


class _FallbackConst:
    """Copy of the values in const.py, used when the script runs outside a checkout."""

    DEFAULT_COUNTRY = "US"
    DEFAULT_LANGUAGE = "en"
    CLIENT_ID = "HondaLinkAndroidApp0074"
    CLIENT_SECRET = "rETFrZcLyUycsSblksCP"
    DEVICE_DESCRIPTION = "Android"
    APP_USER_AGENT = "HondaLink/5.0.51 (Android)"
    IDENTITY_BASE = "https://identity.services.honda.com"
    API_BASE = "https://wsc.hondaweb.com"
    HONDALINK_BUSINESS_ID = "HONDALINK CONNECT"
    HONDALINK_SYSTEM_ID = "com.honda.hondalink.cv_android"
    HONDA_HEADER_VERSION = "1.0"


def load_const():
    path = Path(__file__).resolve().parent.parent / "const.py"
    if not path.exists():
        return _FallbackConst
    spec = importlib.util.spec_from_file_location("hondalink_const", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_const()


def mask_vin(vin: str) -> str:
    vin = str(vin or "")
    return vin[:8] + "*" * max(len(vin) - 8, 0)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for value in (
        payload.get("errorCode"),
        payload.get("ErrorCode"),
        (payload.get("responseBody") or {}).get("errorCode") if isinstance(payload.get("responseBody"), dict) else None,
        (payload.get("Header") or {}).get("ErrorCode") if isinstance(payload.get("Header"), dict) else None,
    ):
        if value not in (None, ""):
            return str(value)
    return None


def error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    body = payload.get("responseBody") if isinstance(payload.get("responseBody"), dict) else {}
    header = payload.get("Header") if isinstance(payload.get("Header"), dict) else {}
    for value in (
        payload.get("errorMessage"),
        payload.get("message"),
        body.get("errorMessage"),
        header.get("ErrorDescription"),
    ):
        if value:
            text = str(value)
            return text if len(text) <= 240 else text[:237] + "..."
    return None


class Client:
    def __init__(self) -> None:
        self.client_reg_key = ""
        self.access_token = ""
        self.country = C.DEFAULT_COUNTRY
        self.language = C.DEFAULT_LANGUAGE
        self.hidas_ident = ""
        self.device_id = str(uuid.uuid4())
        self.session_id = str(uuid.uuid4())

    def _send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                status, text = response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode("utf-8", "replace")
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"_non_json_body": text[:200]}
        return status, payload

    def identity(self, path: str, form: dict[str, str]) -> tuple[int, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": C.APP_USER_AGENT,
        }
        body = urllib.parse.urlencode(form).encode()
        return self._send("POST", f"{C.IDENTITY_BASE}{path}", headers, body)

    def api(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": C.APP_USER_AGENT,
            "hondaHeaderType.version": C.HONDA_HEADER_VERSION,
            "hondaHeaderType.messageId": str(uuid.uuid4()),
            "hondaHeaderType.siteId": self.client_reg_key,
            "hondaHeaderType.businessId": C.HONDALINK_BUSINESS_ID,
            "hondaHeaderType.systemId": C.HONDALINK_SYSTEM_ID,
            "hondaHeaderType.collectedTimestamp": now(),
            "hondaHeaderType.collectedTimeStamp": now(),
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
        return self._send(method, f"{C.API_BASE}{path}", headers, body)

    def login(self, email: str, password: str) -> None:
        status, data = self.identity(
            "/hidas/rs/client/register",
            {"client_id": C.CLIENT_ID, "client_secret": C.CLIENT_SECRET},
        )
        try:
            self.client_reg_key = data["clientregistrationkey"]["client_reg_key"]
        except (KeyError, TypeError):
            sys.exit(f"Client registration failed: HTTP {status} errorCode={error_code(data)}")

        status, data = self.identity(
            "/hidas/rs/token/generate",
            {
                "username": email,
                "password": password,
                "description": C.DEVICE_DESCRIPTION,
                "client_reg_key": self.client_reg_key,
            },
        )
        token = (data or {}).get("token") or {}
        if data.get("request_status") != "success" or not token.get("access_token"):
            sys.exit(f"Login failed: HTTP {status} errorCode={error_code(data)} {error_message(data) or ''}")
        user = data.get("user") or {}
        self.access_token = token["access_token"]
        self.country = user.get("country_code") or self.country
        self.language = user.get("language_code") or self.language
        self.hidas_ident = user.get("hidas_ident") or ""


def report_response(label: str, status: int, payload: Any) -> None:
    line = f"    {label}: HTTP {status}"
    if isinstance(payload, dict) and payload.get("status") is not None:
        line += f", status={payload.get('status')!r}"
    code = error_code(payload)
    if code:
        line += f", errorCode={code}"
    print(line)
    message = error_message(payload)
    if message and (code or status >= 400):
        print(f"      message: {message}")


def probe_vehicle(client: Client, vehicle: dict[str, Any]) -> None:
    vin = str(vehicle.get("VIN") or "")
    print(f"\nVehicle {mask_vin(vin)}")
    for field in VEHICLE_FIELDS:
        print(f"  {field}: {vehicle.get(field)!r}")

    print("  FeatureList:")
    status, data = client.api("GET", f"/REST/NGT/FeatureList/1.0/{vin}")
    features = (((data or {}).get("VEHICLE") or {}).get("Model") or {}).get("Feature") if isinstance(data, dict) else None
    if status == 200 and isinstance(features, list):
        dashboard: list[str] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            name = str(feature.get("FeatureName") or "")
            if "dashboard" in name.lower() and name not in dashboard:
                dashboard.append(name)
            print(f"    - {name} [{feature.get('PackageName') or '-'}]")
        # Feature IDs alone do not identify the backend (a 2021 Clarity lists both
        # 50024 "Remote Dashboard" and 50063 "GTC Dashboard I"); TelematicsPlatform does.
        print(f"    Dashboard features: {dashboard or 'none'}")
    else:
        report_response("FeatureList", status, data)

    print("  Entitlement:")
    status, data = client.api(
        "POST", "/REST/NGT/Enrollment/VehicleEntitlement/2.0", {"device": vin, "vin": vin}
    )
    body = (data or {}).get("responseBody") if isinstance(data, dict) else None
    if status == 200 and isinstance(body, dict):
        print(f"    enrollmentStatus: {body.get('enrollmentStatus')!r}")
        print(f"    tspProvider: {body.get('tspProvider')!r}")
    else:
        report_response("VehicleEntitlement", status, data)

    print("  CIG dashboard (POST /REST/NGT/CIG/dbd/latest):")
    for label, make_body in DASHBOARD_BODIES:
        status, data = client.api("POST", f"/REST/NGT/CIG/dbd/latest/{vin}", make_body(vin))
        report_response(label, status, data)
        body = (data or {}).get("responseBody") if isinstance(data, dict) else None
        if status == 200 and isinstance(body, dict):
            print(f"      responseBody keys: {sorted(body.keys())}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vin", help="only probe this VIN")
    args = parser.parse_args()

    email = os.environ.get("HONDALINK_EMAIL") or input("HondaLink email: ").strip()
    password = os.environ.get("HONDALINK_PASSWORD") or getpass.getpass("HondaLink password: ")

    client = Client()
    client.login(email, password)
    print("Login: OK")

    status, data = client.api("GET", "/REST/NGT/MyVehicle/1.0")
    vehicles = (data or {}).get("vehicleInfo") if isinstance(data, dict) else None
    if status != 200 or not isinstance(vehicles, list):
        report_response("MyVehicle", status, data)
        sys.exit(1)
    if args.vin:
        vehicles = [v for v in vehicles if str(v.get("VIN", "")).upper() == args.vin.strip().upper()]
    print(f"Vehicles on account: {len(vehicles)}")
    for vehicle in vehicles:
        if isinstance(vehicle, dict):
            probe_vehicle(client, vehicle)

    print("\nPlease paste this output into https://github.com/daviddelahoz/ha-hondalink/issues/4")


if __name__ == "__main__":
    main()
