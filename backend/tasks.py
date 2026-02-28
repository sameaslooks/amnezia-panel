import asyncio
from logger import logger
import database as db
from connection import LocalConnection, SSHConnection
from awg_manager import AmneziaWGServer

async def collect_stats_periodically(interval: int = 60):
    while True:
        logger.debug("Starting periodic stats collection")
        try:
            servers = await db.get_all_servers()
            for srv in servers:
                if not srv['is_active']:
                    continue
                logger.debug(f"Collecting stats for server {srv['id']}")
                try:
                    if srv['auth_type'] == 'local':
                        conn = LocalConnection()
                    else:
                        conn = SSHConnection(
                            host=srv['host'],
                            port=srv['port'],
                            username=srv['username'],
                            password=srv.get('password'),
                            private_key=srv.get('private_key')
                        )
                    server = AmneziaWGServer(conn, server_id=srv['id'])
                    await server.collect_traffic_stats()
                    await conn.close()
                except Exception as e:
                    logger.error(f"Stats collection failed for server {srv['id']}: {e}")
        except Exception as e:
            logger.error(f"Periodic stats collection error: {e}")
        await asyncio.sleep(interval)