# main.py
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
import os
import asyncio

from auth import authenticate_user, create_access_token, decode_token
import database as db
from awg_manager import AmneziaWGServer
from connection import LocalConnection, SSHConnection
from logger import setup_logger, logger
from tasks import collect_stats_periodically
from schemas import (
    LoginRequest, TokenResponse, ClientCreate, ExpiryDateRequest,
    UserCreate, UserUpdate, ServerCreate, ServerUpdate,
    DashboardRequest, ServerStatusItem
)
from stats import get_dashboard_stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    task = asyncio.create_task(collect_stats_periodically())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


setup_logger()

app = FastAPI(title="Amnezia Panel", lifespan=lifespan)

static_dir = "/frontend/static"
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Static directory mounted at {static_dir}")
else:
    logger.warning(f"Static directory {static_dir} not found")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


async def _create_server_connection(server_data: dict):
    if server_data['auth_type'] == 'local':
        conn = LocalConnection()
        logger.debug(f"Using local connection for server {server_data['id']}")
    else:
        conn = SSHConnection(
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data.get('password'),
            private_key=server_data.get('private_key'),
            sudo_password=server_data.get('password') if server_data['auth_type'] == 'password' else None
        )
        logger.debug(f"Using SSH connection for server {server_data['id']} ({server_data['host']})")
    return conn


async def get_server(server_id: int = 1, current_user: dict = Depends(get_current_user)):
    server_data = await db.get_server(server_id)
    if not server_data:
        raise HTTPException(status_code=404, detail="Server not found")
    conn = await _create_server_connection(server_data)
    return AmneziaWGServer(conn, server_id=server_id)


async def get_server_public(server_id: int = 1):
    """Для публичных эндпоинтов без проверки админа."""
    server_data = await db.get_server(server_id)
    if not server_data:
        raise HTTPException(status_code=404, detail="Server not found")
    conn = await _create_server_connection(server_data)
    return AmneziaWGServer(conn, server_id=server_id)


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
async def get_clients(
    server: AmneziaWGServer = Depends(get_server),
    current_user: dict = Depends(get_current_user)
):
    if current_user.get("role") == "admin":
        return await server.get_clients()
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    return await server.get_clients(user_id=user_data["id"])


@app.post("/api/clients")
async def create_client(
    client: ClientCreate,
    server: AmneziaWGServer = Depends(get_server),
    admin: dict = Depends(get_current_admin)
):
    try:
        return await server.add_client(client.name, client.user_id)
    except Exception as e:
        if str(e) == "Config limit reached for this user":
            raise HTTPException(status_code=400, detail=str(e))
        logger.error(f"Unexpected error adding client: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/api/clients")
async def delete_client(
    public_key: str,
    server_id: Optional[int] = None,
    admin: dict = Depends(get_current_admin)
):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    if server_id is None:
        client = await db.get_client_by_public_key(decoded_key)
        if client:
            server_id = client['server_id']
        else:
            raise HTTPException(status_code=404, detail="Client not found in database, specify server_id")
    server = await get_server(server_id=server_id, current_user=admin)
    await server.delete_client(decoded_key)
    return {"message": "Client deleted successfully"}


@app.get("/api/traffic")
async def get_traffic(
    server_id: Optional[int] = None,
    admin: dict = Depends(get_current_admin)
):
    if server_id is None:
        server = await get_server(server_id=1, current_user=admin)
    else:
        server = await get_server(server_id=server_id, current_user=admin)
    return await server.get_traffic()


@app.get("/api/user-config")
async def get_user_config(public_key: str, server: AmneziaWGServer = Depends(get_server)):
    config = await server.get_client_config(public_key)
    if not config:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"config": config}


@app.get("/api/limits")
async def get_limits(admin: dict = Depends(get_current_admin)):
    clients = await db.get_all_clients_with_user_info()
    result = []
    for c in clients:
        result.append({
            "public_key": c["public_key"],
            "user_id": c["user_id"],
            "username": c["username"],
            "name": c["name"],
            "ip": c["ip"],
            "limit": c["traffic_limit_bytes"],
            "used": c["traffic_used_bytes"] or 0,
            "expiry_date": c["expiry_date"],
            "is_active": c["is_active"],
            "server_id": c["server_id"],
            "server_name": c["server_name"]
        })
    return result


@app.post("/api/limits")
async def set_limit(
    public_key: str,
    limit_bytes: int,
    server_id: Optional[int] = None,
    admin: dict = Depends(get_current_admin)
):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    client = await db.get_client_by_public_key(decoded_key)
    if not client:
        if server_id is None:
            raise HTTPException(status_code=404, detail="Client not found, specify server_id")
        server = await get_server(server_id=server_id, current_user=admin)
        client_info = await server.get_client_info(decoded_key)
        if not client_info:
            raise HTTPException(status_code=404, detail="Client not found in server config")
        raise HTTPException(status_code=400, detail="Client exists in config but not in DB. Please fetch clients first.")
    user_id = client['user_id']
    await db.update_user_limit(user_id, limit_bytes)
    server_instance = await get_server(server_id=client['server_id'], current_user=admin)
    await db.sync_user_clients_with_limits(user_id, server_instance)
    return {"message": "Limit set"}


@app.post("/api/clients/expiry")
async def set_client_expiry_endpoint(
    public_key: str,
    expiry: ExpiryDateRequest,
    server_id: Optional[int] = None,
    admin: dict = Depends(get_current_admin)
):
    from urllib.parse import unquote
    decoded_key = unquote(public_key)
    client = await db.get_client_by_public_key(decoded_key)
    if not client:
        if server_id is None:
            raise HTTPException(status_code=404, detail="Client not found, specify server_id")
        server = await get_server(server_id=server_id, current_user=admin)
        client_info = await server.get_client_info(decoded_key)
        if not client_info:
            raise HTTPException(status_code=404, detail="Client not found in server config")
        raise HTTPException(status_code=400, detail="Client exists in config but not in DB. Please fetch clients first.")
    user_id = client['user_id']
    await db.update_user_expiry(user_id, expiry.expiry_date)
    server_instance = await get_server(server_id=client['server_id'], current_user=admin)
    await db.sync_user_clients_with_limits(user_id, server_instance)
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
    await db.update_user(
        user_id, 
        user.username, 
        user.password, 
        user.role,
        user.config_limit
    )
    return {"message": "User updated"}


@app.delete("/api/users/{user_id}")
async def delete_user_endpoint(user_id: int, admin: dict = Depends(get_current_admin)):
    await db.delete_user(user_id)
    return {"message": "User deleted"}

@app.get("/api/user/profile")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    clients = await db.get_user_clients(user_data["id"])
    return {
        "id": user_data["id"],
        "username": user_data["username"],
        "role": user_data["role"],
        "traffic_limit_bytes": user_data.get("traffic_limit_bytes"),
        "traffic_used_bytes": user_data.get("traffic_used_bytes", 0),
        "expiry_date": user_data.get("expiry_date"),
        "config_limit": user_data.get("config_limit", 1),
        "clients_count": len(clients)
    }

@app.get("/api/user/clients")
async def get_my_clients(
    current_user: dict = Depends(get_current_user),
    server_id: Optional[int] = None
):
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user_data["id"]
    if server_id:
        server = await get_server_public(server_id=server_id)
        clients = await server.get_clients(user_id=user_id)
    else:
        servers = await db.get_all_servers()
        all_clients = []
        for srv in servers:
            if not srv['is_active']:
                continue
            server = await get_server_public(server_id=srv['id'])
            clients = await server.get_clients(user_id=user_id)
            all_clients.extend(clients)
        clients = all_clients
    return clients


@app.post("/api/user/clients")
async def create_my_client(
    client: ClientCreate,
    server_id: int = 1,
    current_user: dict = Depends(get_current_user)
):
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user_data["id"]
    if not await db.can_create_config(user_id):
        raise HTTPException(status_code=400, detail="Config limit reached")
    server = await get_server_public(server_id)
    return await server.add_client(client.name, user_id)


@app.delete("/api/user/clients/{client_id}")
async def delete_my_client(
    client_id: int,
    current_user: dict = Depends(get_current_user)
):
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    client = await db.get_client_by_id(client_id)
    if not client or client['user_id'] != user_data["id"]:
        raise HTTPException(status_code=404, detail="Client not found")
    server = await get_server_public(client['server_id'])
    await server.delete_client(client['public_key'])
    return {"message": "Client deleted"}


@app.get("/api/user/servers")
async def get_user_servers(current_user: dict = Depends(get_current_user)):
    """Возвращает список серверов, доступных пользователю для создания клиентов."""
    servers = await db.get_all_servers()
    return [
        {"id": s["id"], "name": s["name"]} 
        for s in servers 
        if s.get("is_active")
    ]


@app.get("/api/user/traffic")
async def get_my_traffic(
    current_user: dict = Depends(get_current_user),
    days: int = 30
):
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    stats = await db.get_user_traffic_stats(user_data["id"], days)
    return stats


@app.get("/api/user/traffic-now")
async def get_user_traffic_now(current_user: dict = Depends(get_current_user)):
    """Возвращает текущие данные трафика для клиентов пользователя."""
    user_data = await db.get_user_by_username(current_user["sub"])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")
    clients = await db.get_user_clients(user_data["id"])
    servers = await db.get_all_servers()
    result = []
    for server in servers:
        if not server.get('is_active'):
            continue
        from main import get_server_public
        server_instance = await get_server_public(server['id'])
        traffic_data = await server_instance.get_traffic()
        user_keys = [c['public_key'] for c in clients if c['server_id'] == server['id']]
        for t in traffic_data:
            if t['public_key'] in user_keys:
                result.append(t)
    return result


@app.get("/api/admin/stats")
async def admin_stats(admin: dict = Depends(get_current_admin)):
    try:
        stats = await get_dashboard_stats()
        return stats
    except Exception as e:
        logger.error(f"Failed to get dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/stats")
async def admin_stats_post(
    request: DashboardRequest,
    admin: dict = Depends(get_current_admin)
):
    try:
        stats = await get_dashboard_stats(request.server_statuses)
        return stats
    except Exception as e:
        logger.error(f"Failed to get dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
async def get_server_status(
    server_id: int,
    server: AmneziaWGServer = Depends(get_server),
    admin: dict = Depends(get_current_admin)
):
    try:
        status = await server.get_full_status()
        return status
    except Exception as e:
        logger.error(f"Failed to get status for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/servers/{server_id}/stop")
async def stop_server_container(
    server_id: int,
    server: AmneziaWGServer = Depends(get_server),
    admin: dict = Depends(get_current_admin)
):
    try:
        success = await server.stop_container()
        if success:
            return {"message": "Container stopped"}
        else:
            raise HTTPException(status_code=500, detail="Failed to stop container")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/servers/{server_id}/start")
async def start_server_container(
    server_id: int,
    server: AmneziaWGServer = Depends(get_server),
    admin: dict = Depends(get_current_admin)
):
    try:
        success = await server.start_container()
        if success:
            return {"message": "Container started"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start container")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/servers/{server_id}/restart")
async def restart_server_container(
    server_id: int,
    server: AmneziaWGServer = Depends(get_server),
    admin: dict = Depends(get_current_admin)
):
    try:
        success = await server.restart_container()
        if success:
            return {"message": "Container restarted"}
        else:
            raise HTTPException(status_code=500, detail="Failed to restart container")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    logger.warning(f"HTTP {exc.status_code} on {request.url.path}: {exc.detail}")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Internal error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error, please try again later."})