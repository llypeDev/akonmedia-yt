# Compact dates the way Brazilian interfaces abbreviate them:
# "20 de out. de 2026", "20 de out. de 2026, 14:05", "out. de 2026".
# Built on the lowercase month abbreviation ("b"): Django's pt_BR catalog
# capitalises full month names, which Portuguese does not do mid-sentence.
# Everything else comes from Django's own pt_BR formats.
SHORT_DATE_FORMAT = r"j \d\e b. \d\e Y"
SHORT_DATETIME_FORMAT = r"j \d\e b. \d\e Y, H:i"
YEAR_MONTH_FORMAT = r"b. \d\e Y"
