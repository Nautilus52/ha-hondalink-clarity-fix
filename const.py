from datetime import timedelta

DOMAIN = "hondalink"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_PIN = "pin"
CONF_VIN = "vin"
CONF_NAME = "name"
CONF_CLIENT_REG_KEY = "client_reg_key"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"
CONF_COUNTRY = "country"
CONF_LANGUAGE = "language"
CONF_HIDAS_IDENT = "hidas_ident"
CONF_DEVICE_ID = "device_id"
CONF_SESSION_ID = "session_id"
CONF_VEHICLE_INFO = "vehicle_info"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LOCK_COMMAND = "lock_command"
CONF_UNLOCK_COMMAND = "unlock_command"

DEFAULT_NAME = "HondaLink"
DEFAULT_COUNTRY = "US"
DEFAULT_LANGUAGE = "en"
DEFAULT_SCAN_INTERVAL = 3
DEFAULT_LOCK_COMMAND = "alk"
DEFAULT_UNLOCK_COMMAND = "dulk"
LEGACY_LOCK_COMMAND = "lck"
LEGACY_UNLOCK_COMMAND = "ulk"
DEFAULT_DASHBOARD_FILTERS = [
    "DigitalTwin",
    "VEHICLE RANGE",
    "odometer",
    "TIRE PRESSURE",
]
DASHBOARD_FILTER_SETS = (
    DEFAULT_DASHBOARD_FILTERS,
    ["DigitalTwin"],
    [],
)

# Values of the vehicle record's "TelematicsPlatform" field. These name telematics
# generations, not model years (a 2025 CR-V reports "MY21").
#
# CIG platforms: full dashboard and CIG remote commands. The HondaLink app 5.5.3
# treats MY21, MY22.5 and MY23 this way.
CIG_TELEMATICS_PLATFORMS = ("MY21", "MY22.5", "MY23")
# EV-dashboard platforms: the same /CIG/dbd/latest endpoint, but the app sends an
# "evInfoRequest" body and reads responseBody.evStatus. Without that body the
# gateway answers HTTP 400 / 0001-01-1151 (issue #4). 2ZSPHEV = Clarity Plug-In
# Hybrid (constant TELEMATICS_PLATFORM_CLARITY in the app). CIG remote commands do
# not apply to these vehicles; the app sends theirs through the older GTC backend.
EV_DASHBOARD_PLATFORMS = ("2ZSPHEV",)

DASHBOARD_MODE_CIG = "cig"
DASHBOARD_MODE_EV = "ev"

CLIENT_ID = "HondaLinkAndroidApp0074"
CLIENT_SECRET = "rETFrZcLyUycsSblksCP"
DEVICE_DESCRIPTION = "Android"
APP_USER_AGENT = "HondaLink/5.0.51 (Android)"

IDENTITY_BASE = "https://identity.services.honda.com"
API_BASE = "https://wsc.hondaweb.com"

HONDALINK_BUSINESS_ID = "HONDALINK CONNECT"
HONDALINK_SYSTEM_ID = "com.honda.hondalink.cv_android"
HONDA_HEADER_VERSION = "1.0"

UPDATE_INTERVAL = timedelta(minutes=DEFAULT_SCAN_INTERVAL)

DATA_API = "api"
DATA_COORDINATOR = "coordinator"
