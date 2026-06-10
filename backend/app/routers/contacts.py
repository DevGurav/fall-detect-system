"""Emergency contacts — GET/POST/DELETE /v1/contacts.

Each caregiver account can register up to 10 escalation contacts (name + phone +
priority).  When an alert goes unacknowledged, the backend will eventually call
these (Phase 32).  For now this is purely a data layer: CRUD with RLS isolation
so a user can only see/edit their own contacts.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.auth import get_current_user
from app.deps import require_db
from app.models import EmergencyContact
from app.schemas import ContactIn, ContactOut

router = APIRouter(prefix="/v1/contacts", tags=["contacts"])


@router.get("", response_model=list[ContactOut])
async def list_contacts(
    request: Request, user_id: UUID = Depends(get_current_user)
) -> list[ContactOut]:
    require_db(request)
    async with request.app.state.db.session_for(user_id) as session:
        rows = (
            await session.execute(
                select(EmergencyContact)
                .where(EmergencyContact.user_id == user_id)
                .order_by(EmergencyContact.priority)
            )
        ).scalars().all()
    return [ContactOut.model_validate(r) for r in rows]


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(
    req: ContactIn,
    request: Request,
    user_id: UUID = Depends(get_current_user),
) -> ContactOut:
    require_db(request)
    async with request.app.state.db.session_for(user_id) as session:
        contact = EmergencyContact(
            user_id=user_id,
            name=req.name,
            phone=req.phone,
            priority=req.priority,
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)
    return ContactOut.model_validate(contact)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: UUID,
    request: Request,
    user_id: UUID = Depends(get_current_user),
) -> None:
    require_db(request)
    async with request.app.state.db.session_for(user_id) as session:
        contact = await session.get(EmergencyContact, contact_id)
        if contact is None or contact.user_id != user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
        await session.delete(contact)
        await session.commit()
