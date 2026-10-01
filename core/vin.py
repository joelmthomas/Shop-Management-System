import requests

NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"


def decode_vin(vin):
    """Return {'year', 'make', 'model'} for a VIN, or None if it can't be decoded."""
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        return None
    try:
        resp = requests.get(NHTSA_URL.format(vin=vin), params={"format": "json"}, timeout=5)
        resp.raise_for_status()
        data = resp.json()["Results"][0]
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None

    year = (data.get("ModelYear") or "").strip()
    make = (data.get("Make") or "").strip()
    model = (data.get("Model") or "").strip()
    if not (make and model):
        return None

    return {
        "year": int(year) if year.isdigit() else None,
        "make": make.title() if len(make) > 3 else make,
        "model": model,
    } 