"""Canonicalize drifting column headers to stable snake_case names.

Different export batches spell headers differently ("lead Source" vs
"Lead Source" vs "LeadSource"). Matching key = lowercase + strip everything
non-alphanumeric, so all spelling/casing/spacing variants of a header resolve
to the same canonical field.
"""

from __future__ import annotations

import re

import pandas as pd


def _key(header: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


# canonical field -> matching keys (the _key() of every known spelling variant)
_ALIASES: dict[str, list[str]] = {
    "name": ["name", "leadname", "customername"],
    "email": ["email", "emailaddress", "emailid"],
    "duplicacy_check": ["duplicacycheck", "duplicatecheck", "duplicacy"],
    "phone": ["phone", "phonenumber", "phoneno", "mobile", "mobilenumber", "contactnumber"],
    "stage": ["stage", "leadstage"],
    "lead_source": ["leadsource", "source"],
    "form": ["form"],
    # pandas auto-suffixes the second "Form" header as "Form.1" -> key "form1".
    # Its meaning is unconfirmed; keep it as a separate, unlabeled field.
    "form_2": ["form1", "form2"],
    "first_call_date": ["firstcalldate", "firstcall"],
    "latest_call_date": ["latestcalldate", "lastcalldate", "latestcall"],
    # 'Call Status', 'Latest Call Status', 'Lastest Call Status' (typo in real data)
    "call_status": ["callstatus", "latestcallstatus", "lastcallstatus", "latesttcallstatus"],
    "visit_status": ["visitstatus", "sitevisitstatus", "visit"],
    "buying_status": ["buyingstatus", "buyerstatus"],
    "interest_level": ["interestlevel", "interest"],
    "budget": ["budget", "budgetrange"],
    "purpose_of_buying": ["purposeofbuying", "buyingpurpose", "purpose"],
    "configuration_required": ["configurationrequired", "configurationrequirement", "configuration", "config", "bhkrequired"],
    # "Size Preference" is a separate field (bungalow size / plot area), not config
    "size_preference": ["sizepreference", "sizerequirement", "sizepreferred"],
    "location_preference": ["locationpreference", "preferredlocation"],
    "qualitative_remarks": ["qualitativeremarks", "remarks", "remark", "comments"],
    "follow_up_date": ["followupdate", "followup", "nextfollowupdate"],
    "business_profile": ["businessprofile", "profession", "occupation"],
    "hwc": ["hwc"],
    "client_status": ["clientstatus", "clientstatusnote"],
    "properties_seen": ["propertiesseen", "propertyseen", "projectsseen"],
    "current_location": [
        "currentlocationiffromahmedabad",
        "currentlocation",
        "currentarea",
    ],
    "marital_status": ["maritalstatus", "marital"],
    "kids": ["kids", "children", "noofkids"],
    "family_size": ["familysize", "familymembers", "nooffamilymembers"],
    "looking_since": ["lookingsince", "searchingsince"],
    "current_size": ["currentsize", "currenthomesize", "currentconfiguration"],
    "reason_for_shifting": ["reasonforshifting", "shiftingreason", "reasonofshifting"],
    "current_city": ["currentcity", "city"],
    "lead_for_project": ["leadforproject", "project", "projectname"],
    "campaign": ["campaign", "campaignname", "utmcampaign"],
}

_KEY_TO_CANONICAL: dict[str, str] = {
    key: canon for canon, keys in _ALIASES.items() for key in keys
}

# Every canonical field from the known schema, in schema order — used to
# guarantee downstream code never KeyErrors on a missing column.
CANONICAL_FIELDS: list[str] = list(_ALIASES.keys())


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Rename recognized headers to canonical names; leave unknown ones as-is.

    If two source columns resolve to the same canonical name (beyond the known
    Form/Form.1 pair, which pandas already suffixes), later ones get _2/_3
    suffixes rather than silently colliding.
    """
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for col in df.columns:
        canon = _KEY_TO_CANONICAL.get(_key(col), f"{col}")
        if canon in seen:
            seen[canon] += 1
            canon = f"{canon}_{seen[canon]}"
        else:
            seen[canon] = 1
        new_cols.append(canon)
    out = df.copy()
    out.columns = new_cols
    return out


def ensure_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add any missing canonical column as all-NA so analytics never KeyError."""
    out = df.copy()
    for field in CANONICAL_FIELDS:
        if field not in out.columns:
            out[field] = pd.NA
    return out


def matched_canonical_count(df: pd.DataFrame) -> int:
    """How many of this (already-normalized) sheet's columns are canonical fields."""
    return sum(1 for col in df.columns if col in CANONICAL_FIELDS)


def looks_like_lead_sheet(df: pd.DataFrame, min_matches: int = 5) -> bool:
    """Heuristic: a sheet is lead data if enough headers match the known schema.

    A threshold of 5 is required to avoid false-positives on ancillary sheets
    like 'Daily Report' (Name + Phone + 3 custom cols) or 'not spoken'
    (Name/Email/Phone only) or 'Mayuri Warm Cases' (Name/Phone/HWC only).
    """
    return matched_canonical_count(df) >= min_matches
