from fastapi import FastAPI, HTTPException, status, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from contextlib import asynccontextmanager
import os
import asyncio

from auth import authenticate_user, create_access_token, decode_token
import database as db
from awg_manager import AmneziaWGServer
from connection import LocalConnection, SSHConnection
from logger import setup_logger, logger
from tasks import collect_stats_periodically

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(collect_stats_periodically())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

setup_logger()

app = FastAPI(title="Amnezia Panel", lifespan=lifespan)

# Статика
static_dir = "/frontend/static"
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Static directory mounted at {static_dir}")
else:
    logger.warning(f"Static directory {static_dir} not found")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Зависимости ----------
async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth_header.split(" ")[1]
    payload = decode_token(token)
    if not payload:
        logger.warning(f"Invalid token from {request.client.host}")
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload

async def get_current_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        logger.warning(f"Non-admin user {current_user.get('sub')} attempted admin action")
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user

async def get_server(server_id: int = 1, admin: dict = Depends(get_current_admin)):
    """Возвращает экземпляр AmneziaWGServer для указанного сервера."""
    server_data = await db.get_server(server_id)
    if not server_data:
        raise HTTPException(status_code=404, detail="Server not found")
    if server_data['auth_type'] == 'local':
        conn = LocalConnection()
        logger.debug(f"Using local connection for server {server_id}")
    else:
        conn = SSHConnection(
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data.get('password'),
            private_key=server_data.get('private_key'),
            sudo_password=server_data.get('password') if server_data['auth_type'] == 'password' else None
        )
        logger.debug(f"Using SSH connection for server {server_id} ({server_data['host']})")
    return AmneziaWGServer(conn, server_id=server_id)

# ---------- Модели ----------
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str

class ClientCreate(BaseModel):
    name: str

class ExpiryDateRequest(BaseModel):
    expiry_date: Optional[str] = None

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"

class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None

class ServerCreate(BaseModel):
    name: str
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    auth_type: str = "local"
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

# ---------- Эндпоинты ----------
@app.post("/api/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    logger.info(f"Login attempt for user {request.username}")
    user = await authenticate_user(request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    logger.info(f"User {request.username} logged in successfully")
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}

@app.get("/api/verify-token")
async def verify_token(current_user: dict = Depends(get_current_user)):
    return {"username": current_user.get("sub"), "role": current_user.get("role")}

@app.get("/api/clients")
async def get_clients(server: AmneziaWGServer = Depends(get_server)):
    return await server.get_clients()

@app.post("/api/clients")
async def create_client(client: ClientCreate, server: AmneziaWGServer = Depends(get_server)):
    return await server.add_client(client.name)

@app.delete("/api/clients")
async def delete_client(public_key: str, server: AmneziaWGServer = Depends(get_server)):
    await server.delete_client(public_key)
    return {"message": "Client deleted successfully"}

@app.get("/api/traffic")
async def get_traffic(server: AmneziaWGServer = Depends(get_server)):
    return await server.get_traffic()

@app.get("/api/user-config")
async def get_user_config(public_key: str, server: AmneziaWGServer = Depends(get_server)):
    config = await server.get_client_config(public_key)
    if not config:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"config": config}

@app.get("/api/limits")
async def get_limits(admin: dict = Depends(get_current_admin)):
    clients = await db.get_all_clients()
    result = []
    for c in clients:
        result.append({
            "public_key": c["public_key"],
            "name": c["name"],
            "ip": c["ip"],
            "limit": c["traffic_limit_bytes"],          # было traffic_limit_bytes
            "used": c["traffic_used_bytes"] or 0,       # было traffic_used_bytes
            "expiry_date": c["expiry_date"],
            "is_active": c["is_active"],
            "server_id": c["server_id"],
            "server_name": c["server_name"]
        })
    return result

@app.post("/api/limits")
async def set_limit(public_key: str, limit_bytes: int, server: AmneziaWGServer = Depends(get_server)):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    logger.info(f"Received set_limit request for {decoded_key[:8]}... with limit {limit_bytes}")
    client_info = await server.get_client_info(decoded_key)
    if not client_info:
        raise HTTPException(status_code=404, detail="Client not found in server config")
    await db.upsert_client(decoded_key, client_info['name'], client_info['ip'], server.server_id)
    await db.set_client_limit(decoded_key, limit_bytes)
    await server.unblock_client(decoded_key)
    logger.info(f"Client {decoded_key[:8]}... unblocked after setting limit")
    return {"message": "Limit set"}

@app.post("/api/clients/expiry")
async def set_client_expiry_endpoint(
    public_key: str,
    expiry: ExpiryDateRequest,
    server: AmneziaWGServer = Depends(get_server)
):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    logger.info(f"Setting expiry for {decoded_key[:8]}... to {expiry.expiry_date}")
    client_info = await server.get_client_info(decoded_key)
    if not client_info:
        raise HTTPException(status_code=404, detail="Client not found in server config")
    await db.upsert_client(decoded_key, client_info['name'], client_info['ip'], server.server_id)
    await db.set_client_expiry(decoded_key, expiry.expiry_date)
    return {"message": "Expiry date set"}

@app.post("/api/clients/{public_key}/activate")
async def activate_client_endpoint(public_key: str, admin: dict = Depends(get_current_admin)):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    await db.activate_client(decoded_key)
    return {"message": "Client activated"}

@app.post("/api/clients/{public_key}/deactivate")
async def deactivate_client_endpoint(public_key: str, admin: dict = Depends(get_current_admin)):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    await db.deactivate_client(decoded_key)
    return {"message": "Client deactivated"}

@app.post("/api/iptables/sync")
async def sync_iptables(server: AmneziaWGServer = Depends(get_server)):
    await server.sync_iptables_with_db()
    return {"message": "iptables synchronized"}

@app.get("/api/generate-link")
async def generate_vpn_link(public_key: str, server: AmneziaWGServer = Depends(get_server)):
    link = await server.generate_amnezia_vpn_link(public_key)
    return {"link": link}

# ---------- Управление пользователями ----------
@app.get("/api/users")
async def get_users(admin: dict = Depends(get_current_admin)):
    return await db.get_all_users()

@app.post("/api/users")
async def create_user_endpoint(user: UserCreate, admin: dict = Depends(get_current_admin)):
    if await db.create_user(user.username, user.password, user.role):
        return {"message": "User created"}
    raise HTTPException(status_code=400, detail="User already exists")

@app.put("/api/users/{user_id}")
async def update_user_endpoint(user_id: int, user: UserUpdate, admin: dict = Depends(get_current_admin)):
    await db.update_user(user_id, user.username, user.password, user.role)
    return {"message": "User updated"}

@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, admin: dict = Depends(get_current_admin)):
    await db.delete_user(user_id)
    return {"message": "User deleted"}

# ---------- Управление серверами ----------
@app.get("/api/servers")
async def get_servers(admin: dict = Depends(get_current_admin)):
    return await db.get_all_servers()

@app.post("/api/servers")
async def create_server(server: ServerCreate, admin: dict = Depends(get_current_admin)):
    server_id = await db.add_server(server.dict())
    return {"id": server_id, "message": "Server created"}

@app.put("/api/servers/{server_id}")
async def update_server(server_id: int, server: ServerUpdate, admin: dict = Depends(get_current_admin)):
    await db.update_server(server_id, server.dict(exclude_unset=True))
    return {"message": "Server updated"}

@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: int, admin: dict = Depends(get_current_admin)):
    try:
        await db.delete_server(server_id)
        return {"message": "Server deleted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/servers/{server_id}/test")
async def test_server_connection(server_id: int, admin: dict = Depends(get_current_admin)):
    server_data = await db.get_server(server_id)
    if not server_data:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        if server_data['auth_type'] == 'local':
            conn = LocalConnection()
        else:
            conn = SSHConnection(
                host=server_data['host'],
                port=server_data['port'],
                username=server_data['username'],
                password=server_data.get('password'),
                private_key=server_data.get('private_key')
            )
        awg = AmneziaWGServer(conn, server_id)
        await awg.conn.run_command("echo 'test'")
        return {"status": "ok", "message": "Connection successful"}
    except Exception as e:
        logger.error(f"Server {server_id} connection test failed: {e}")
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

@app.get("/api/servers/{server_id}/status")
async def get_server_status(server_id: int, admin: dict = Depends(get_current_admin)):
    server_data = await db.get_server(server_id)
    if not server_data:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        if server_data['auth_type'] == 'local':
            conn = LocalConnection()
        else:
            conn = SSHConnection(
                host=server_data['host'],
                port=server_data['port'],
                username=server_data['username'],
                password=server_data.get('password'),
                private_key=server_data.get('private_key')
            )
        awg = AmneziaWGServer(conn, server_id)
        status = {
            "online": True,
            "container_running": False,
            "version": None,
            "clients_count": 0,
            "errors": []
        }
        res = await awg.conn.run_command("docker ps --filter name=amnezia-awg2 --format '{{.Status}}'")
        if 'Up' in res:
            status["container_running"] = True
            version = await awg.conn.run_command("awg version 2>/dev/null || echo 'unknown'")
            status["version"] = version.strip()
            clients = await awg.get_clients()
            status["clients_count"] = len(clients)
        return status
    except Exception as e:
        logger.error(f"Failed to get status for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------- WebSocket для установки сервера (временно заглушка) ----------
@app.websocket("/api/servers/{server_id}/setup-ws")
async def websocket_setup_server(websocket: WebSocket, server_id: int):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        token = data.get("token")
        if not token:
            await websocket.send_json({"type": "error", "message": "No token provided"})
            await websocket.close()
            return
        payload = decode_token(token)
        if not payload or payload.get("role") != "admin":
            await websocket.send_json({"type": "error", "message": "Unauthorized"})
            await websocket.close()
            return
        server_data = await db.get_server(server_id)
        if not server_data:
            await websocket.send_json({"type": "error", "message": "Server not found"})
            await websocket.close()
            return
        sudo_password = server_data.get('password')
        if server_data['auth_type'] == 'local':
            await websocket.send_json({"type": "error", "message": "Setup not supported for local server"})
            await websocket.close()
            return
        conn = SSHConnection(
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data.get('password'),
            private_key=server_data.get('private_key'),
            sudo_password=sudo_password
        )
        server = AmneziaWGServer(conn, server_id=server_id)
        async for update in server.setup_server_stream(sudo_password):
            await websocket.send_json(update)

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from server {server_id} setup")
    except Exception as e:
        logger.error(f"WebSocket setup error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass

# ---------- Статические страницы ----------
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

# ---------- Обработчики исключений ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    logger.warning(f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Internal error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error, please try again later."})

# ---------- Startup ----------
@app.on_event("startup")
async def startup():
    setup_logger()
    await db.init_db()
    logger.info("Application started, debug mode: %s", os.getenv("DEBUG", "False"))