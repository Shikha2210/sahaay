from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from app.config import settings
from app.db import db

pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth = OAuth2PasswordBearer(tokenUrl='/api/auth/login')

def hash_password(value: str) -> str: return pwd.hash(value)
def verify_password(value: str, hashed: str) -> bool: return pwd.verify(value, hashed)
def create_token(user: dict) -> str:
    payload={'sub':str(user['_id']),'role':user['role'],'email':user['email'],'exp':datetime.now(timezone.utc)+timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')

def current_user(token: str = Depends(oauth)):
    try: payload=jwt.decode(token, settings.jwt_secret, algorithms=['HS256'])
    except JWTError: raise HTTPException(401,'Invalid or expired access token')
    user=db.users.find_one({'_id':__import__('bson').ObjectId(payload['sub']), 'active':True})
    if not user: raise HTTPException(401,'Account is unavailable')
    return user

def require_roles(*roles):
    def dependency(user=Depends(current_user)):
        if user['role'] not in roles: raise HTTPException(403,'Insufficient role permissions')
        return user
    return dependency
