from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..security import hash_password
from ..templating import templates
from .. import models

router = APIRouter()


def _require_admin(user):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return None


@router.get("/users", response_class=HTMLResponse)
def list_users(request: Request, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    redirect = _require_admin(user)
    if redirect:
        return redirect
    users = db.query(models.User).order_by(models.User.username).all()
    return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": users})


@router.post("/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redirect = _require_admin(user)
    if redirect:
        return redirect
    existing = db.query(models.User).filter(models.User.username == username).first()
    if not existing:
        db.add(models.User(username=username, password_hash=hash_password(password), is_admin=0))
        db.commit()
    return RedirectResponse("/users", status_code=303)
