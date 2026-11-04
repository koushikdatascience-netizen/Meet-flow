"""Domestic state choices from the official GST e-way-bill state master.
Source: https://docs.ewaybillgst.gov.in/apidocs/state-code.html
Codes are stored as two-character strings. Historical invoices are unchanged.
"""

STATE_CHOICES = (
    ("01", "Jammu and Kashmir"),
    ("02", "Himachal Pradesh"),
    ("03", "Punjab"),
    ("04", "Chandigarh"),
    ("05", "Uttarakhand"),
    ("06", "Haryana"),
    ("07", "Delhi"),
    ("08", "Rajasthan"),
    ("09", "Uttar Pradesh"),
    ("10", "Bihar"),
    ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"),
    ("13", "Nagaland"),
    ("14", "Manipur"),
    ("15", "Mizoram"),
    ("16", "Tripura"),
    ("17", "Meghalaya"),
    ("18", "Assam"),
    ("19", "West Bengal"),
    ("20", "Jharkhand"),
    ("21", "Odisha"),
    ("22", "Chhattisgarh"),
    ("23", "Madhya Pradesh"),
    ("24", "Gujarat"),
    ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"),
    ("29", "Karnataka"),
    ("30", "Goa"),
    ("31", "Lakshadweep"),
    ("32", "Kerala"),
    ("33", "Tamil Nadu"),
    ("34", "Puducherry"),
    ("35", "Andaman and Nicobar Islands"),
    ("36", "Telangana"),
    ("37", "Andhra Pradesh"),
    ("38", "Ladakh"),
    ("97", "Other territory"),
)
STATE_ALIASES = dict(
    zip(
        "JK HP PB CH UK HR DL RJ UP BR SK AR NL MN MZ TR ML AS WB JH OD CG MP GJ DN MH KA GA LD KL TN PY AN TS AP LA OT".split(),
        [code for code, _ in STATE_CHOICES],
        strict=True,
    )
)


def normalize_state(value):
    value = str(value or "").strip()
    if not value:
        return ""
    codes = dict(STATE_CHOICES)
    if value.isascii() and value.isdigit() and len(value) <= 2:
        result = value.zfill(2)
    else:
        names = {name.casefold(): code for code, name in STATE_CHOICES}
        result = STATE_ALIASES.get(value.upper()) or names.get(value.casefold())
    if result not in codes:
        raise ValueError("Choose a customer state from the list.")
    return result
