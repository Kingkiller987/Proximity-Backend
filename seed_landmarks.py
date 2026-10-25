import sys
sys.path.append("/home/kingp/proximity-backend")
import uuid
from app.database import SessionLocal
from app.models import Landmark
from app.location import make_geography_point

LANDMARKS = [
    {
        "id": uuid.uuid5(uuid.NAMESPACE_DNS, "lm-sbu-campus"),
        "name": "Stony Brook University Campus",
        "area_name": "Staller Steps & Academic Mall",
        "latitude": 40.9150,
        "longitude": -73.1230,
        "radius_meters": 800.0,
        "category": "complex",
    },
    {
        "id": uuid.uuid5(uuid.NAMESPACE_DNS, "lm-staller"),
        "name": "Staller Steps",
        "area_name": "Staller Steps & Academic Mall",
        "latitude": 40.9154,
        "longitude": -73.1225,
        "radius_meters": 180.0,
        "category": "pointOfInterest",
    },
    {
        "id": uuid.uuid5(uuid.NAMESPACE_DNS, "lm-library"),
        "name": "Library North Reading Room",
        "area_name": "Staller Steps & Academic Mall",
        "latitude": 40.9148,
        "longitude": -73.1235,
        "radius_meters": 160.0,
        "category": "venue",
    },
    {
        "id": uuid.uuid5(uuid.NAMESPACE_DNS, "lm-dining"),
        "name": "East Side Dining Patio",
        "area_name": "East Side Dining Commons",
        "latitude": 40.9162,
        "longitude": -73.1208,
        "radius_meters": 160.0,
        "category": "venue",
    },
    {
        "id": uuid.uuid5(uuid.NAMESPACE_DNS, "lm-harbor"),
        "name": "Port Jefferson Harbor Pier",
        "area_name": "Port Jefferson Harbor",
        "latitude": 40.9465,
        "longitude": -73.0691,
        "radius_meters": 220.0,
        "category": "pointOfInterest",
    },
]

db = SessionLocal()
try:
    created = 0
    for data in LANDMARKS:
        existing = db.query(Landmark).filter(Landmark.id == data["id"]).first()
        if not existing:
            pt = make_geography_point(data["longitude"], data["latitude"])
            lm = Landmark(
                id=data["id"],
                name=data["name"],
                area_name=data["area_name"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                location=pt,
                radius_meters=data["radius_meters"],
                category=data["category"],
            )
            db.add(lm)
            created += 1
    db.commit()
    print(f"Successfully seeded {created} landmarks.")
finally:
    db.close()
