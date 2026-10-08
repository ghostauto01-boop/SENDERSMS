"""Authenticated system diagnostics."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.system_error import SystemErrorRecord
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()


def _serialize(row: SystemErrorRecord, *, include_traceback: bool = True) -> dict:
    result = {
        "request_id": row.request_id,
        "code": row.code,
        "exception_type": row.exception_type,
        "message": row.message,
        "method": row.method,
        "path": row.path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_traceback:
        result["traceback"] = row.traceback
    return result


@router.get("/errors")
async def read_errors(
    request_id: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read an error by request id, or list recent errors without stack traces."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    if request_id:
        row = (await db.execute(
            select(SystemErrorRecord).where(SystemErrorRecord.request_id == request_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No stored error for this request_id")
        return _serialize(row)
    rows = list((await db.execute(
        select(SystemErrorRecord).order_by(SystemErrorRecord.created_at.desc()).limit(limit)
    )).scalars().all())
    return {"items": [_serialize(row, include_traceback=False) for row in rows]}
