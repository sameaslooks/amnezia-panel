# tasks.py
import asyncio
from logger import logger
import database as db
from connection import LocalConnection, SSHConnection
from awg_manager import AmneziaWGServer

async def check_limits_and_sync_all_servers(servers_list):
    """
    Проверяет лимиты всех пользователей и деактивирует клиентов на всех серверах при превышении.
    """
    exceeded_traffic = await db.get_users_exceeded_traffic()
    expired = await db.get_users_expired()
    problem_users = set(exceeded_traffic + expired)
    if not problem_users:
        return

    server_instances = {}
    for srv in servers_list:
        if not srv['is_active']:
            continue
        conn = None
        try:
            if srv['auth_type'] == 'local':
                conn = LocalConnection()
            else:
                conn = SSHConnection(
                    host=srv['host'],
                    port=srv['port'],
                    username=srv['username'],
                    password=srv.get('password'),
                    private_key=srv.get('private_key'),
                    sudo_password=srv.get('password')
                )
            server_instances[srv['id']] = AmneziaWGServer(conn, server_id=srv['id'])
        except Exception as e:
            logger.error(f"Failed to create server instance for {srv['id']}: {e}")
            if conn:
                await conn.close()

    for user_id in problem_users:
        clients_by_server = await db.get_user_clients_grouped_by_server(user_id)
        for server_id, clients in clients_by_server.items():
            server = server_instances.get(server_id)
            if not server:
                continue
            for client in clients:
                if client['is_active']:
                    await server.block_client(client['public_key'])
                    await db.deactivate_client(client['id'])
        logger.info(f"Deactivated all clients for user {user_id} due to limits")

    for server in server_instances.values():
        await server.conn.close()

async def collect_stats_periodically(interval: int = 60):
    while True:
        logger.debug("Starting periodic stats collection")
        try:
            servers = await db.get_all_servers_full()
            for srv in servers:
                if not srv['is_active']:
                    continue
                conn = None
                try:
                    if srv['auth_type'] == 'local':
                        conn = LocalConnection()
                    else:
                        conn = SSHConnection(
                            host=srv['host'],
                            port=srv['port'],
                            username=srv['username'],
                            password=srv.get('password'),
                            private_key=srv.get('private_key'),
                            sudo_password=srv.get('password')
                        )
                    server = AmneziaWGServer(conn, server_id=srv['id'])
                    await server.collect_traffic_stats()
                except Exception as e:
                    logger.error(f"Stats collection failed for server {srv['id']}: {e}", exc_info=True)
                finally:
                    if conn:
                        await conn.close()

            await check_limits_and_sync_all_servers(servers)

        except Exception as e:
            logger.error(f"Periodic stats collection error: {e}", exc_info=True)
        await asyncio.sleep(interval)