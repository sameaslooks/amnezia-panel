# tasks.py
import asyncio
from logger import logger
import database as db
from connection import LocalConnection, SSHConnection
from awg_manager import AmneziaWGServer


async def collect_stats_periodically(interval: int = 60):
    while True:
        logger.debug("Starting periodic stats collection")
        try:
            servers = await db.get_all_servers_full()
            for srv in servers:
                if not srv['is_active']:
                    continue
                logger.debug(f"Collecting stats for server {srv['id']} ({srv['name']})")
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
                    # После обновления трафика проверяем лимиты для этого сервера
                    await db.check_all_limits(server_instance=server)
                    await conn.close()
                except Exception as e:
                    logger.error(f"Stats collection failed for server {srv['id']}: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Periodic stats collection error: {e}", exc_info=True)
        await asyncio.sleep(interval)