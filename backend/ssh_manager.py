import asyncssh
import asyncio
import os

class SSHManager:
    async def test_connection(self, host, port, user, ssh_key_path, passphrase=None):
        """Проверяет подключение по SSH"""
        try:
            # Пробуем подключиться с ключом (и passphrase если есть)
            if passphrase:
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    client_keys=[(ssh_key_path, passphrase)],
                    known_hosts=None
                )
            else:
                # Пробуем без passphrase
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    client_keys=[ssh_key_path],
                    known_hosts=None
                )
            
            # Получаем информацию о системе
            result = await conn.run("uname -a")
            system_info = result.stdout.strip()
            
            await conn.close()
            
            return {
                "success": True,
                "system_info": system_info
            }
            
        except asyncssh.Error as e:
            # Если ошибка "key requires passphrase"
            if "key requires passphrase" in str(e):
                return {
                    "success": False,
                    "need_passphrase": True,
                    "error": "Key requires passphrase"
                }
            else:
                return {
                    "success": False,
                    "need_passphrase": False,
                    "error": str(e)
                }