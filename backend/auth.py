import jwt
import os
from datetime import datetime, timedelta
from typing import Optional

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"

if SECRET_KEY == "your-secret-key-change-this":
    print("⚠️ WARNING: Using default JWT secret! Set JWT_SECRET in production!")

def authenticate_user(username: str, password: str):
    """Проверяет учётные данные пользователя"""
    from database import get_user_by_username
    user = get_user_by_username(username)
    if user and user["password"] == password:
        return user
    return None

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None