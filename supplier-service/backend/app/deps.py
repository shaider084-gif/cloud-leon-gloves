from typing import Optional

from fastapi import Request, Depends
from sqlalchemy.orm import Session

from .database import get_db
from .security import read_session_token
from . import models


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[models.User]:
    token = request.cookies.get("session")
    if not token:
        return None
    data = read_session_token(token)
    if not data:
        return None
    return db.get(models.User, data.get("user_id"))
