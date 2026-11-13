"""The row of open lookups above Device 360. Kept in the person's session."""

SESSION_KEY = "open_lookups"
MAX_OPEN = 6


def opened(session):
    return list(session.get(SESSION_KEY, []))


def open_tab(session, key, label):
    tabs = [tab for tab in opened(session) if tab["key"] != key]
    tabs.append({"key": key, "label": label})
    session[SESSION_KEY] = tabs[-MAX_OPEN:]


def close_tab(session, key):
    session[SESSION_KEY] = [tab for tab in opened(session) if tab["key"] != key]
