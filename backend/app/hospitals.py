"""
Hospital Finder Module for Med-Drishti
Provides nearby hospital search using Haversine distance formula.
"""
import json
import math
import os
from typing import Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "hospitals_data.json")

def _load_hospitals() -> list:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)["hospitals"]

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in kilometres between two lat/lng points."""
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def get_nearby_hospitals(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
    limit: int = 10,
    hospital_type: Optional[str] = None,
) -> list:
    """
    Return up to `limit` hospitals within `radius_km` of (lat, lng),
    sorted by distance ascending.
    """
    hospitals = _load_hospitals()
    results = []
    for h in hospitals:
        dist = _haversine_km(lat, lng, h["lat"], h["lng"])
        if dist <= radius_km:
            if hospital_type and hospital_type.lower() not in h["type"].lower():
                continue
            results.append({**h, "distance_km": round(dist, 1)})
    results.sort(key=lambda x: x["distance_km"])
    return results[:limit]

def get_all_hospitals(state: Optional[str] = None) -> list:
    """Return all hospitals, optionally filtered by state."""
    hospitals = _load_hospitals()
    if state:
        hospitals = [h for h in hospitals if h["state"].lower() == state.lower()]
    return hospitals

def search_hospitals(query: str) -> list:
    """Full-text search across name, city, state, and specialties."""
    q = query.lower().strip()
    hospitals = _load_hospitals()
    return [
        h for h in hospitals
        if q in h["name"].lower()
        or q in h["city"].lower()
        or q in h["state"].lower()
        or any(q in s.lower() for s in h["specialties"])
    ]
