# auth.py
import jwt
import os
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from logger import logger

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"

if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET environment variable not set")
if SECRET_KEY == "your-secret-key-change-this":
    logger.warning("Using default JWT secret! Set JWT_SECRET in production!")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def authenticate_user(username: str, password: str):
    from database import get_user_by_username
    user = await get_user_by_username(username)
    if user and verify_password(password, user["password_hash"]):
        logger.info(f"User {username} authenticated successfully")
        return user
    logger.warning(f"Failed authentication attempt for user {username}")
    return None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    logger.debug(f"Created access token for {data.get('sub')}")
    return token


def decode_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        logger.debug(f"Decoded token for {payload.get('sub')}")
        return payload
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid token: {e}")
        return None