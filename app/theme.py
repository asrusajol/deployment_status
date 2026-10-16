"""The two company themes from the design reference (Deployment Tracker V12.1).

The choice is per browser, kept in a cookie rather than on the User row: it needs no
migration, and it also applies to /login, where nobody is known yet. The cookie name
matches the reference mock's localStorage key so the two read the same.
"""

THEME_COOKIE = "dt-theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # a year — it's a display preference, not a credential

DEFAULT_THEME = "standard"

# Order is the order of the theme menu in the shell bar.
THEMES = {
    "standard": "Standard (Morning Horizon)",
    "schertech": "Schertech",
}
