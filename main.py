import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents

app = FastAPI(title="Reconnect API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------- Utility -------------------------

def collection(name: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    return db[name]


def now_utc():
    return datetime.now(timezone.utc)


# ------------------------- Models -------------------------

class ContactIn(BaseModel):
    fullName: str
    relationship: str
    phoneNumber: str
    email: Optional[str] = None
    frequencyDays: int = 30
    lastContactedAt: Optional[datetime] = None
    priority: Optional[int] = 1


class ContactOut(ContactIn):
    id: str


class InteractionIn(BaseModel):
    type: str  # "call" | "text"
    messagePreview: Optional[str] = None
    notes: Optional[str] = None


class InteractionOut(BaseModel):
    id: str
    contactId: str
    type: str
    messagePreview: Optional[str] = None
    notes: Optional[str] = None
    createdAt: datetime


class SettingsIn(BaseModel):
    mode: str = "daily"  # daily | weekly
    countDaily: int = 3
    countWeekly: int = 10
    defaultFrequencies: List[int] = [7, 30, 90]


# ------------------------- Root & Health -------------------------

@app.get("/")
def read_root():
    return {"message": "Reconnect API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["collections"] = db.list_collection_names()
        else:
            response["database"] = "❌ Not Connected"
    except Exception as e:
        response["database"] = f"Error: {str(e)[:80]}"
    return response


# ------------------------- Contacts CRUD -------------------------

@app.get("/api/contacts")
def list_contacts():
    items = get_documents("contact")
    for it in items:
        it["id"] = str(it.pop("_id"))
    return items


@app.post("/api/contacts")
def create_contact(data: ContactIn):
    payload = data.model_dump()
    _id = create_document("contact", payload)
    doc = collection("contact").find_one({"_id": __import__("bson").ObjectId(_id)})
    if doc:
        doc["id"] = str(doc.pop("_id"))
        return doc
    return {"id": _id, **payload}


@app.put("/api/contacts/{contact_id}")
def update_contact(contact_id: str, data: ContactIn):
    from bson import ObjectId

    result = collection("contact").update_one(
        {"_id": ObjectId(contact_id)}, {"$set": {**data.model_dump(), "updated_at": now_utc()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    doc = collection("contact").find_one({"_id": ObjectId(contact_id)})
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: str):
    from bson import ObjectId

    res = collection("contact").delete_one({"_id": ObjectId(contact_id)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Contact not found")
    # Also delete interactions for this contact
    collection("contactinteraction").delete_many({"contactId": contact_id})
    return {"success": True}


# ------------------------- Interactions -------------------------

@app.post("/api/contacts/{contact_id}/interactions")
def add_interaction(contact_id: str, data: InteractionIn):
    from bson import ObjectId

    # Make sure contact exists
    if not collection("contact").find_one({"_id": ObjectId(contact_id)}):
        raise HTTPException(status_code=404, detail="Contact not found")

    payload = {
        "contactId": contact_id,
        "type": data.type,
        "messagePreview": data.messagePreview,
        "notes": data.notes,
        "createdAt": now_utc(),
    }
    _id = create_document("contactinteraction", payload)

    # Update lastContactedAt on contact
    collection("contact").update_one(
        {"_id": ObjectId(contact_id)}, {"$set": {"lastContactedAt": now_utc(), "updated_at": now_utc()}}
    )

    doc = collection("contactinteraction").find_one({"_id": ObjectId(_id)})
    if doc:
        doc["id"] = str(doc.pop("_id"))
        return doc
    return {"id": _id, **payload}


@app.get("/api/interactions")
def list_interactions(limit: int = 100):
    items = collection("contactinteraction").find().sort("createdAt", -1).limit(limit)
    result = []
    for it in items:
        it["id"] = str(it.pop("_id"))
        result.append(it)
    return result


@app.get("/api/contacts/{contact_id}/interactions")
def list_interactions_for_contact(contact_id: str):
    items = collection("contactinteraction").find({"contactId": contact_id}).sort("createdAt", -1)
    result = []
    for it in items:
        it["id"] = str(it.pop("_id"))
        result.append(it)
    return result


# ------------------------- Suggestions & Templates -------------------------

def days_since(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    return (now_utc() - dt).days


def is_due(contact: dict) -> bool:
    """Return True if a contact is due to be contacted based on frequencyDays and lastContactedAt."""
    freq = contact.get("frequencyDays", 30)
    last = contact.get("lastContactedAt")
    if isinstance(last, str):
        try:
            last = datetime.fromisoformat(last)
        except Exception:
            last = None
    if last is None:
        return True
    return (now_utc() - last).days >= freq


TEMPLATES = [
    "Hi {name}, it’s been a while! I hope you’ve been well. Just wanted to check in and see how things are going.",
    "Hey {name}, I was thinking of you and realized it’s been some time since we last connected. How have you been?",
    "Hi {name}, hope everything’s going well. It’s been a bit since we spoke — would love to catch up when you have a moment.",
]


@app.get("/api/templates")
def get_templates(name: str = "there"):
    return [t.format(name=name) for t in TEMPLATES]


@app.get("/api/suggestions")
def get_suggestions(mode: str = "daily", count: int = 3):
    # Load contacts
    items = list(collection("contact").find())
    for it in items:
        it["id"] = str(it.pop("_id"))

    # Sort by how overdue they are
    def overdue_score(c: dict):
        last = c.get("lastContactedAt")
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last)
            except Exception:
                last = None
        freq = c.get("frequencyDays", 30)
        if last is None:
            return 1e9  # never contacted → top priority
        return (now_utc() - last).days - freq

    items.sort(key=overdue_score, reverse=True)

    # Filter to due or near-due
    due = [c for c in items if is_due(c)]
    fallback = items

    target = count if mode == "daily" else count
    result = (due or fallback)[:target]

    # Add helper fields
    for c in result:
        last = c.get("lastContactedAt")
        if isinstance(last, str):
            try:
                last = datetime.fromisoformat(last)
            except Exception:
                last = None
        c["daysSince"] = days_since(last)
    return result


# ------------------------- Settings -------------------------

@app.get("/api/settings")
def get_settings():
    doc = collection("settings").find_one({"_id": "default"})
    if not doc:
        doc = {
            "_id": "default",
            "mode": "daily",
            "countDaily": 3,
            "countWeekly": 10,
            "defaultFrequencies": [7, 30, 90],
        }
        collection("settings").insert_one(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.put("/api/settings")
def update_settings(data: SettingsIn):
    payload = data.model_dump()
    collection("settings").update_one(
        {"_id": "default"},
        {"$set": payload},
        upsert=True,
    )
    doc = collection("settings").find_one({"_id": "default"})
    doc["id"] = str(doc.pop("_id"))
    return doc


# ------------------------- Seed Demo Data -------------------------

@app.post("/api/seed")
def seed_demo():
    """Seed a few example contacts for demo purposes."""
    existing = collection("contact").count_documents({})
    if existing > 0:
        return {"seeded": False, "message": "Contacts already exist"}

    contacts = [
        {
            "fullName": "Alex Johnson",
            "relationship": "friend",
            "phoneNumber": "+15551234567",
            "email": "alex@example.com",
            "frequencyDays": 14,
            "lastContactedAt": now_utc() - timedelta(days=30),
            "priority": 3,
        },
        {
            "fullName": "Jamie Lee",
            "relationship": "business",
            "phoneNumber": "+15557654321",
            "email": "jamie@work.co",
            "frequencyDays": 30,
            "lastContactedAt": now_utc() - timedelta(days=90),
            "priority": 4,
        },
        {
            "fullName": "Taylor Kim",
            "relationship": "family",
            "phoneNumber": "+15559876543",
            "email": None,
            "frequencyDays": 7,
            "lastContactedAt": None,
            "priority": 5,
        },
    ]

    ids = []
    for c in contacts:
        ids.append(create_document("contact", c))
    return {"seeded": True, "count": len(ids), "ids": ids}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
