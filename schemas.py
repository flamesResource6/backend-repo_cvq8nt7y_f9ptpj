"""
Database Schemas for Reconnect

Define your MongoDB collection schemas here using Pydantic models.
Each class maps to a MongoDB collection with the lowercase class name.

Collections used:
- contact
- contactinteraction
- settings
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal
from datetime import datetime


class Contact(BaseModel):
    """
    Contacts you want to keep in touch with.
    Collection name: "contact"
    """
    fullName: str = Field(..., description="Full name of the contact")
    relationship: Literal["friend", "family", "business", "other"] = Field(
        "friend", description="Relationship category"
    )
    phoneNumber: str = Field(..., description="Phone number for calling or texting")
    email: Optional[EmailStr] = Field(None, description="Email address (optional)")
    frequencyDays: int = Field(30, ge=1, description="How often to reach out in days")
    lastContactedAt: Optional[datetime] = Field(
        None, description="When you last contacted this person"
    )
    priority: Optional[int] = Field(
        1, ge=1, le=5, description="Optional priority 1 (low) to 5 (high)"
    )


class ContactInteraction(BaseModel):
    """
    Logs of interactions with contacts.
    Collection name: "contactinteraction"
    """
    contactId: str = Field(..., description="Related contact id (string ObjectId)")
    type: Literal["call", "text"] = Field(..., description="Type of interaction")
    messagePreview: Optional[str] = Field(
        None, description="First part of the message for text interactions"
    )
    notes: Optional[str] = Field(None, description="Optional notes for calls")
    createdAt: Optional[datetime] = Field(None, description="When the interaction happened")


class Settings(BaseModel):
    """
    App-wide settings.
    Collection name: "settings"
    A single document can be used; use _id = "default" when creating.
    """
    mode: Literal["daily", "weekly"] = Field("daily", description="Default mode")
    countDaily: int = Field(3, ge=1, le=50, description="How many to contact per day")
    countWeekly: int = Field(10, ge=1, le=200, description="How many to contact per week")
    defaultFrequencies: list[int] = Field(
        default_factory=lambda: [7, 30, 90],
        description="Default frequency options (days)",
    )
