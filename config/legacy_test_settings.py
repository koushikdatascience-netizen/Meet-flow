# Test harness for preserved legacy financial history; never used by the live app.
from .settings import *

ROOT_URLCONF = "config.legacy_test_urls"
INSTALLED_APPS = [value for value in INSTALLED_APPS if value != "inventory"]
MIDDLEWARE = [
    value for value in MIDDLEWARE if value != "inventory.middleware.LegacyAdminGuard"
]
import copy

TEMPLATES = copy.deepcopy(TEMPLATES)
TEMPLATES[0]["OPTIONS"]["context_processors"] = [
    value
    for value in TEMPLATES[0]["OPTIONS"]["context_processors"]
    if value != "inventory.context.navigation"
]
