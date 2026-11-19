"""Conservative cross-provider basketball fixture identity checks."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Callable, Any


def _repair(value: Any) -> str:
    text = str(value or "")
    try:
        from ftfy import fix_text
        text = fix_text(text)
    except Exception:
        pass
    return text


def _normal(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _repair(value).casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    translit = str.maketrans({
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z",
        "и":"i","й":"j","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
        "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh",
        "щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"
    })
    value = value.translate(translit)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def _timestamp(value: Any):
    if value is None:
        return None
    try:
        number = float(value)
        if number > 100000000000:
            number /= 1000.0
        if number > 1000000000:
            return number
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _gender(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if any(x in text for x in ("women", "woman", "female", "ladies", "girls", "wbb", "wnba", "женск", "девуш")):
        return "women"
    if any(x in text for x in ("men", "mens", "male", "мужск", "мужчин")):
        return "men"
    return "unknown"


def _age(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    found = re.search(r"(?:u|under)[ -]?(17|18|19|20|21|23)", text)
    if found:
        return "u" + found.group(1)
    if any(x in text for x in ("junior", "youth", "academy", "молодеж", "молодёж", "юнош")):
        return "youth"
    return "senior_or_unknown"


def _competition_key(value: Any, normalize: Callable[[Any], str] | None = None,
                     repair: Callable[[Any], str] | None = None) -> str:
    raw = (repair or _repair)(value or "")
    key = (normalize or _normal)(raw)
    return re.sub(r"(basketball|league|championship|tournament|division|conference|cup|men|male|female|season)", "", key)


def match_context_compatible(pari: dict, external: dict,
                             normalize: Callable[[Any], str] | None = None,
                             repair: Callable[[Any], str] | None = None) -> bool:
    """Require compatible event context in addition to a two-team name match."""
    pari = pari if isinstance(pari, dict) else {}
    external = external if isinstance(external, dict) else {}
    pari_league = pari.get("league") or pari.get("competitionName") or pari.get("sport_name")
    ext_league = external.get("league") or external.get("competition") or external.get("tournament")
    pg = _gender(pari.get("team1"), pari.get("team2"), pari_league, pari.get("sport_name"))
    eg = _gender(external.get("home"), external.get("away"), ext_league, external.get("status"))
    if pg == "women" or eg == "women":
        return False
    if pg != "unknown" and eg != "unknown" and pg != eg:
        return False
    pa = _age(pari_league, pari.get("team1"), pari.get("team2"))
    ea = _age(ext_league, external.get("home"), external.get("away"))
    if pa != ea and (pa != "senior_or_unknown" or ea != "senior_or_unknown"):
        return False
    pari_live = pari.get("external_live") if isinstance(pari.get("external_live"), dict) else {}
    pari_length = pari.get("period_length_seconds") or pari_live.get("period_length_seconds")
    ext_length = external.get("period_length_seconds")
    try:
        if pari_length and ext_length and int(pari_length) != int(ext_length):
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    pari_count = pari.get("period_count") or pari_live.get("period_count")
    ext_count = external.get("period_count")
    try:
        if pari_count and ext_count and int(pari_count) != int(ext_count):
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    pts = _timestamp(pari.get("start_time") or pari.get("startTime"))
    ets = _timestamp(external.get("start_time") or external.get("startTimestamp"))
    same_fixture_window = pts is not None and ets is not None and abs(pts - ets) <= 36 * 3600
    if pts is not None and ets is not None and not same_fixture_window:
        return False
    if pari_league and ext_league:
        a = _competition_key(pari_league, normalize, repair)
        b = _competition_key(ext_league, normalize, repair)
        ta, tb = set(re.findall(r"[a-z0-9]{2,}", a)), set(re.findall(r"[a-z0-9]{2,}", b))
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        similarity = SequenceMatcher(None, a, b).ratio() if a and b else 0.0
        suffix_match = len(min(a, b, key=len)) >= 3 and (a.endswith(b) or b.endswith(a))
        return overlap >= 0.5 or similarity >= 0.45 or suffix_match
    return same_fixture_window
