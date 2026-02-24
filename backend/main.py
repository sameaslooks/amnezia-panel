from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect
import os

from database import activate_client, deactivate_client, check_and_deactivate_overlimit, get_all_users, create_user, update_user, delete_user

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

class ServerCreate(BaseModel):
    name: str
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    auth_type: str = "local"  # local, password, key, key+sudo
    password: Optional[str] = None
    private_key: Optional[str] = None

class ServerUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    auth_type: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    is_active: Optional[bool] = None


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
async def get_clients(request: Request, server_id: Optional[int] = None):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    awg = AWGManager(server_id=server_id or 1)
    return await awg.get_clients()

@app.post("/api/clients")
async def create_client(client: ClientCreate, request: Request, server_id: Optional[int] = None):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    awg = AWGManager(server_id=server_id or 1)
    return await awg.add_client(client.name)

@app.get("/api/traffic")
async def get_traffic(request: Request, server_id: Optional[int] = None):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    awg = AWGManager(server_id=server_id or 1)
    return await awg.get_traffic()

@app.get("/api/user-config")
async def get_user_config(public_key: str, request: Request, server_id: Optional[int] = None):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    awg = AWGManager(server_id=server_id or 1)
    config = await awg.get_client_config(public_key)
    if not config:
        raise HTTPException(status_code=404, detail="Client not found")
    
    return {"config": config}
   
@app.delete("/api/clients")
async def delete_client(public_key: str, request: Request, server_id: Optional[int] = None):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    try:
        awg = AWGManager(server_id=server_id or 1)
        await awg.delete_client(public_key)
        return {"message": "Client deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ========== LIMITS FUNCTIONS ==========
@app.get("/api/limits")
async def get_limits(request: Request, server_id: Optional[int] = None):
    payload = await verify_token(request)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    # Получаем всех клиентов из AWG
    awg = AWGManager(server_id=server_id or 1)
    clients = await awg.get_clients()
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
async def set_limit(public_key: str, limit_bytes: int, request: Request, server_id: Optional[int] = None):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    from database import set_client_limit
    
    set_client_limit(decoded_key, limit_bytes)
    
    awg = AWGManager(server_id=server_id or 1)
    await awg.unblock_client(decoded_key)
    
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
async def sync_iptables(request: Request, server_id: Optional[int] = None):
    payload = get_token_payload(request)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    awg = AWGManager(server_id=server_id or 1)
    await awg.sync_iptables_with_db()
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
async def generate_vpn_link(public_key: str, request: Request, server_id: Optional[int] = None):
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
    
    awg = AWGManager(server_id=server_id or 1)
    link = await awg.generate_amnezia_vpn_link(
        client_ip=client["ip"],
        client_private_key=client["private_key"],
        client_public_key=public_key
    )
    
    return {"link": link}

# ========== MULTI-SERVER MANAGEMENT ==========
@app.get("/api/servers")
async def get_servers(request: Request):
    """Получает список всех серверов"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import get_all_servers
    return get_all_servers()

@app.post("/api/servers")
async def create_server(server: ServerCreate, request: Request):
    """Создаёт новый сервер"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import add_server
    server_id = add_server(server.dict())
    return {"id": server_id, "message": "Server created successfully"}

@app.put("/api/servers/{server_id}")
async def update_server(server_id: int, server: ServerUpdate, request: Request):
    """Обновляет сервер"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import update_server
    update_server(server_id, server.dict(exclude_unset=True))
    return {"message": "Server updated successfully"}

@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: int, request: Request):
    """Удаляет сервер"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import delete_server
    try:
        delete_server(server_id)
        return {"message": "Server deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/servers/{server_id}/test")
async def test_server_connection(server_id: int, request: Request):
    """Тестирует подключение к серверу"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import get_server
    server = get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    try:
        # Создаём временный менеджер и пробуем подключиться
        test_awg = AWGManager(server_id=server_id)
        await test_awg._exec_in_container("echo 'test'")
        return {"status": "ok", "message": "Connection successful"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

@app.post("/api/servers/{server_id}/setup")
async def setup_server(server_id: int, request: Request, sudo_password: Optional[str] = None):
    """Устанавливает AmneziaWG на удалённый сервер"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import get_server
    server = get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    try:
        awg = AWGManager(server_id=server_id)
        result = await awg.setup_server(sudo_password)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/api/servers/{server_id}/setup-ws")
async def websocket_setup_server(websocket: WebSocket, server_id: int):
    """WebSocket для установки сервера с real-time логами"""
    await websocket.accept()
    
    try:
        # Получаем токен из первого сообщения
        data = await websocket.receive_json()
        token = data.get("token")
        sudo_password = data.get("sudo_password")
        
        # Проверяем токен
        payload = decode_token(token)
        if not payload or payload.get("role") != "admin":
            await websocket.send_json({"type": "error", "message": "Unauthorized"})
            await websocket.close()
            return
        
        from database import get_server
        server = get_server(server_id)
        if not server:
            await websocket.send_json({"type": "error", "message": "Server not found"})
            await websocket.close()
            return
        
        # Запускаем установку
        awg = AWGManager(server_id=server_id)
        
        async for update in awg.setup_server_stream(sudo_password):
            await websocket.send_json(update)
            
    except WebSocketDisconnect:
        print(f"Client disconnected from server {server_id} setup")
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass

@app.get("/api/servers/{server_id}/status")
async def get_server_status(server_id: int, request: Request):
    """Получает статус сервера"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    from database import get_server
    server = get_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    try:
        awg = AWGManager(server_id=server_id)
        status = await awg.get_server_status()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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