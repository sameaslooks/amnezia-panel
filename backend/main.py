from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from datetime import timedelta
from urllib.parse import unquote
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os

from database import get_all_clients, set_client_limit, activate_client, deactivate_client, check_and_deactivate_overlimit, get_all_users, create_user, update_user, delete_user

from auth import authenticate_user, create_access_token, decode_token
from awg_manager import AWGManager

app = FastAPI(title="Amnezia Panel")

static_dir = "/frontend/static"
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    print(f"Static directory mounted at {static_dir}")
else:
    print(f"Static directory {static_dir} not found")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    # Логируем полную ошибку для себя в консоль
    print(f"!!! Internal Error on {request.url.path}: {exc}")
    # А клиенту отдаём общее сообщение
    return JSONResponse(status_code=500, content={"detail": "Internal server error, please try again later."})

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


awg = AWGManager()

def get_token_payload(request: Request):
    """Helper функция для проверки и получения payload из токена"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header.split(" ")[1]
    return decode_token(token)

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str

class ClientCreate(BaseModel):
    name: str

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"

class UserUpdate(BaseModel):
    username: str = None
    password: str = None
    role: str = None

@app.post("/api/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    user = authenticate_user(request.username, request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    token = create_access_token(
        data={"sub": user["username"], "role": user["role"]}
    )
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user["role"]
    }

@app.get("/api/verify-token")
async def verify_token(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return {"username": payload.get("sub"), "role": payload.get("role")}

@app.get("/api/clients")
async def get_clients(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return awg.get_clients()

@app.post("/api/clients")
async def create_client(client: ClientCreate, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    return awg.add_client(client.name)

@app.get("/api/traffic")
async def get_traffic(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return awg.get_traffic()

@app.get("/api/user-config")
async def get_user_config(public_key: str, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    config = awg.get_client_config(public_key)
    if not config:
        raise HTTPException(status_code=404, detail="Client not found")
    
    return {"config": config}
   
@app.delete("/api/clients")
async def delete_client(public_key: str, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    try:
        awg.delete_client(public_key)
        return {"message": "Client deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ========== LIMITS FUNCTIONS ==========
@app.get("/api/limits")
async def get_limits(request: Request):
    payload = await verify_token(request)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Получаем всех клиентов из AWG
    clients = awg.get_clients()
    result = []
    
    for client in clients:
        # Пробуем получить из БД
        from database import get_client, create_client
        db_client = get_client(client["public_key"])
        
        if not db_client:
            # Если нет в БД - создаём
            create_client(
                client["public_key"], 
                client["name"], 
                client["ip"]
            )
            db_client = get_client(client["public_key"])
        
        result.append({
            "public_key": client["public_key"],
            "name": client["name"],
            "ip": client["ip"],
            "limit": db_client["limit"] if db_client else None,
            "used": db_client["used"] if db_client else 0,
            "is_active": db_client["is_active"] if db_client else True
        })
    
    return result

@app.post("/api/limits")
async def set_limit(public_key: str, limit_bytes: int, request: Request):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    from database import set_client_limit
    
    # Убираем awg из вызова
    set_client_limit(decoded_key, limit_bytes)
    
    # Явно разблокируем через iptables
    awg.unblock_client(decoded_key)
    
    return {"message": "Limit set"}

@app.post("/api/clients/{public_key}/activate")
async def activate_client_endpoint(public_key: str, request: Request):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    activate_client(decoded_key)
    return {"message": "Client activated"}

@app.post("/api/clients/{public_key}/deactivate")
async def deactivate_client_endpoint(public_key: str, request: Request):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    deactivate_client(decoded_key)
    return {"message": "Client deactivated"}

@app.post("/api/cron/check-limits")
async def cron_check_limits(request: Request):
    # Внутренний эндпоинт для cron
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != "Bearer internal-cron-token":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    deactivated = check_and_deactivate_overlimit()
    return {"deactivated": deactivated}

@app.post("/api/iptables/sync")
async def sync_iptables(request: Request):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    awg.sync_iptables_with_db()
    return {"message": "iptables synchronized"}

# ========== USERS MANAGEMENT ==========

@app.get("/api/users")
async def get_users(request: Request):
    """Получает список всех пользователей"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    return get_all_users()

@app.post("/api/users")
async def create_user_endpoint(user: UserCreate, request: Request):
    """Создаёт нового пользователя"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    if create_user(user.username, user.password, user.role):
        return {"message": "User created successfully"}
    else:
        raise HTTPException(status_code=400, detail="User already exists")

@app.put("/api/users/{user_id}")
async def update_user_endpoint(user_id: int, user: UserUpdate, request: Request):
    """Обновляет пользователя"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    update_user(user_id, user.username, user.password, user.role)
    return {"message": "User updated successfully"}

@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, request: Request):
    """Удаляет пользователя"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    delete_user(user_id)
    return {"message": "User deleted successfully"}

@app.get("/api/generate-link")
async def generate_vpn_link(public_key: str, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    from database import get_client
    client = get_client(public_key)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    link = awg.generate_amnezia_vpn_link(
        client_ip=client["ip"],
        client_private_key=client["private_key"],
        client_public_key=public_key
    )
    
    return {"link": link}

# HTML страницы
@app.get("/")
async def root():
    return FileResponse("/frontend/login.html")

@app.get("/login")
async def login_page():
    return FileResponse("/frontend/login.html")

@app.get("/admin")
async def admin_page():
    return FileResponse("/frontend/admin.html")

@app.get("/user")
async def user_page():
    return FileResponse("/frontend/user.html")

@app.get("/users")
async def users_page():
    return FileResponse("/frontend/users.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)