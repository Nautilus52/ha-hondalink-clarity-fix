# Clarity Plug-In Hybrid (`2ZSPHEV`) protocol notes

Recovered from the decompiled HondaLink Android app 5.5.3 (`com.honda.hondalink.connect`,
signed by American Honda Motor Co). Status reading (part 1) is implemented in this
integration and confirmed against a live 2021 Clarity. Part 2's sign-in and
dashboard calls were confirmed live with a read-only probe. **The commands in part 3
have not been sent to a vehicle yet.**

The app's name for this platform is `AppConstants.TELEMATICS_PLATFORM_CLARITY = "2ZSPHEV"`.

## 1. Status: CIG dashboard with an EV body

Same endpoint as other vehicles, different body (`com.honda.common.utils.d1.a`):

```
POST https://wsc.hondaweb.com/REST/NGT/CIG/dbd/latest/{VIN}
Authorization: Bearer <HIDAS access token>
bearerToken: <HIDAS access token>
hondaHeaderType.*: as for other CIG calls

{"evInfoRequest": {"reqType": "all_info", "setUnit": {"dist": "mile", "temp": "F"}}}
```

`setUnit` is `{"dist": "km", "temp": "C"}` when the account country is `CA`. The app adds
`"smartChargeEnrolled": <bool>` only for smart-charge-enrolled vehicles. Sending the CIG
body (`{"fromDate": "", "toDate": ""}`) instead returns HTTP 400 `0001-01-1151`.

The app logs `CIGDataInitializationState for clarity: processState called with TCU and
user is logged In enrolled` before this call (`qj/d.java`).

Reply (`CigNONTCUDashboardResponseBody`): `responseBody` has `evStatus`, `fuelLevel`,
`maintenanceMinder`, `warningLamps`, `timestamp`, `isUploaded`. `evStatus` has
`vehicleInfo`, `acOnTimerInfo`, `commandStatusInfo`. Most `vehicleInfo` fields are
`{"valid": bool, "value": str, "unit": str}`:

| field | meaning | observed (2021 Clarity) |
|---|---|---|
| `soc` | battery state of charge | `32.0` % |
| `evRange` | electric range | `6` mile |
| `petRange` | gasoline range | `61` mile |
| `totalRange` | total range | absent |
| `intTemp` / `extTemp` | cabin / outside temperature | `70` F / `51.0` F |
| `chargeStatus` | `1` = charging (`r50/t.W()`); `0` and `2` are shown as not charging | `0` |
| `plugStatus` | `0` = unplugged | `0` |
| `acStatus` | `1` = climate on | `0` |
| `igStatus` | `1` = ignition on | `0` |
| `chargeStartTime1` / `chargeEndTime1` | charge schedule, `HHMM` (plain strings) | `0500` / `1700` |
| `chargeMode1`, `currentTimerSetting` | charge mode / timer codes (meaning not mapped) | `1` / `0` |
| `curPosLat` / `curPosLon` | position, `"+DD,MM,SS.ss"` (`j0.k0`) | empty |
| `updTime` | data time, `yyyyMMddHHmmss` in **UTC** (`d1.d`) | `20261115161336` |

## 2. Older "GTC" backend (`https://www.jp.hondalink.com`)

Used for Clarity remote commands. Since March 2026 its dashboard returns
`causedBy: "No data is uploaded."` for this vehicle.

### 2.1 Handshake: `GET /api/VinServiceAuth`

Headers (interceptor `ij.p.r`):

```
X-Service-Key: lowercase hex SHA-256("DL2mBuVQsT7d54c2xaDf94jYe8D35c2p" + yyyyMMdd)   # GMT date (j0.h4, j0.U0)
X-App-Id: hondalink.android
X-App-Version: 1.0
X-Device-Id: <stable UUID>   # app: UUID.nameUUIDFromBytes(android_id)
```

The reply sets cookies (`HICSESSIONKEY`, `AWSELB`). Every later GTC call sends them back
(interceptors `ij.q` / `ij.r`). The app logs `processDashboardServiceAuth Success cookies stored`.

### 2.2 Authenticated GTC calls

Headers (interceptor `ij.p.C0331p`, built by `HondaApplication.N/O`):

```
X-App-Id: com.honda.hondalink.connect
X-App-Version: 5.5.3
X-Device-Id: <same UUID>
X-Vin-Auth-Date: yyyy-MM-dd HH:mm:ss   # device local time (j0.V0)
X-Vin: <VIN>
BearerToken: <HIDAS access token>
Cookie: <cookies from VinServiceAuth>
```

Dashboard (read-only): `POST /api/GW/VinService/GetLatestVehicleData`, form
`startTime=&endTime=&type=ALL`.

## 3. Remote commands (not yet tested on a vehicle)

`POST /api/GW/VinService/RegisterRemoteList`, form fields `requestdata=<JSON string>` and
`VIN=<VIN>`, headers as in 2.2, preceded by the 2.1 handshake (`mb0/e.java`, `dm/b.java`).

`requestdata` (`RemoteClarityCommandPostData`, Gson names):

```json
{"VIN": "<VIN>", "rmt_request": {"req_type": "start_acon", "set_start_acon": {"acon_type": "force"}}}
{"VIN": "<VIN>", "rmt_request": {"req_type": "stop_acon"}}
{"VIN": "<VIN>", "rmt_request": {"req_type": "start_charge"}}
{"VIN": "<VIN>", "rmt_request": {"req_type": "stop_charge"}}
{"VIN": "<VIN>", "rmt_request": {"req_type": "charge_timer", "set_charge_timer": {...}}}
{"VIN": "<VIN>", "rmt_request": {"req_type": "status_update"}}
```

`status_update` is what Find My Car sends. The reply (`RemoteCommandClarityResponse`) has
`rmt_data` with `result_status` and per-command `*_info.result_substatus` (e.g.
`acon_running`, `acon_stop`). The app then re-reads the part 1 dashboard and checks
`evStatus.commandStatusInfo` and `acStatus` / `chargeStatus` until they change.

Climate timers go to `POST /api/GW/VinService/AconTimerSettingUpdate`
(`requestdata`, `VIN`).
