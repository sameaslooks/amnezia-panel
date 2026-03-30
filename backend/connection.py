# connection.py
import abc
import asyncio
import subprocess
import tempfile
import os
import asyncssh
from typing import Optional
from logger import logger


class Connection(abc.ABC):
    """Абстрактный класс для выполнения команд на сервере AmneziaWG."""

    @abc.abstractmethod
    async def run_command(self, command: str) -> str:
        """Выполняет команду и возвращает stdout."""
        pass

    @abc.abstractmethod
    async def write_file(self, path: str, content: str) -> bool:
        """Записывает содержимое в файл по указанному пути."""
        pass

    @abc.abstractmethod
    async def close(self):
        """Закрывает соединение (если необходимо)."""
        pass


class LocalConnection(Connection):
    """Подключение к локальному Docker-контейнеру."""

    def __init__(self, container_name: str = "amnezia-awg2"):
        self.container_name = container_name
        logger.debug(f"LocalConnection initialized with container {container_name}")

    async def run_command(self, command: str, in_container: bool = True) -> str:
        logger.debug(f"Local run: {command} (in_container={in_container})")
        try:
            if in_container:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", self.container_name, "bash", "-c", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            else:
                # Выполняем команду на хосте
                proc = await asyncio.create_subprocess_exec(
                    "bash", "-c", command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            stdout, stderr = await proc.communicate()
            if stderr:
                logger.debug(f"Command stderr: {stderr.decode().strip()}")
            return stdout.decode()
        except Exception as e:
            logger.error(f"Local command error: {e}")
            return ""

    async def write_file(self, path: str, content: str, in_container: bool = True) -> bool:
        logger.debug(f"Local write file {path} ({len(content)} bytes)")
        try:
            cmd = f"cat > {path} << 'EOF'\n{content}\nEOF"
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", self.container_name, "bash", "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            success = proc.returncode == 0
            if success:
                logger.debug(f"Successfully wrote {path}")
            else:
                logger.error(f"Failed to write {path}")
            return success
        except Exception as e:
            logger.error(f"Local write file error: {e}")
            return False

    async def close(self):
        pass


class SSHConnection(Connection):
    """Подключение к удалённому серверу через SSH."""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = None,
        password: str = None,
        private_key: str = None,
        sudo_password: str = None
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.sudo_password = sudo_password
        self._conn = None
        self._temp_key_path = None
        logger.debug(f"SSHConnection initialized for {username}@{host}:{port}")

    async def _connect(self):
        if self._conn is not None:
            return
        logger.debug(f"Connecting to {self.username}@{self.host}:{self.port}")
        kwargs = {
            'host': self.host,
            'port': self.port,
            'username': self.username,
            'known_hosts': None
        }
        temp_key_created = False
        try:
            if self.private_key:
                with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                    f.write(self.private_key)
                    self._temp_key_path = f.name
                os.chmod(self._temp_key_path, 0o600)
                kwargs['client_keys'] = [self._temp_key_path]
                temp_key_created = True
                logger.debug("Using private key authentication")
            elif self.password:
                kwargs['password'] = self.password
                kwargs['client_keys'] = None
                logger.debug("Using password authentication")

            self._conn = await asyncssh.connect(**kwargs)
            logger.debug("SSH connection established")
        except Exception as e:
            logger.error(f"SSH connection error: {e}")
            if temp_key_created and self._temp_key_path and os.path.exists(self._temp_key_path):
                os.unlink(self._temp_key_path)
                self._temp_key_path = None
            raise

    async def run_command(self, command: str, in_container: bool = True) -> str:
        await self._connect()
        if in_container:
            escaped = command.replace('"', '\\"')
            cmd = f"docker exec amnezia-awg2 bash -c \"{escaped}\""
        else:
            cmd = command
        if self.sudo_password:
            full_cmd = f"echo '{self.sudo_password}' | sudo -S {cmd}"
            logger.debug(f"SSH run with sudo: {cmd}")
        else:
            full_cmd = cmd
            logger.debug(f"SSH run: {full_cmd}")
        result = await self._conn.run(full_cmd)
        return result.stdout

    async def write_file(self, path: str, content: str, in_container: bool = True) -> bool:
        await self._connect()
        logger.debug(f"SSH write file {'(container) ' if in_container else '(host) '}{path} ({len(content)} bytes)")

        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(content)
            local_path = f.name

        remote_tmp = f"/tmp/awg_{os.path.basename(local_path)}"
        try:
            await asyncssh.scp(local_path, (self._conn, remote_tmp))

            if in_container:
                docker_cmd = f"docker cp {remote_tmp} amnezia-awg2:{path}"
                if self.sudo_password:
                    docker_cmd = f"echo '{self.sudo_password}' | sudo -S {docker_cmd}"
                result = await self._conn.run(docker_cmd)
                success = result.returncode == 0
            else:
                mkdir_cmd = f"sudo mkdir -p {os.path.dirname(path)}"
                if self.sudo_password:
                    mkdir_cmd = f"echo '{self.sudo_password}' | sudo -S {mkdir_cmd}"
                await self._conn.run(mkdir_cmd)

                mv_cmd = f"sudo mv {remote_tmp} {path}"
                if self.sudo_password:
                    mv_cmd = f"echo '{self.sudo_password}' | sudo -S {mv_cmd}"
                result = await self._conn.run(mv_cmd)
                success = result.returncode == 0

            await self._conn.run(f"rm -f {remote_tmp}")

            if success:
                logger.debug(f"Successfully wrote {path}")
            else:
                logger.error(f"Failed to write {path}")
            return success
        except Exception as e:
            logger.error(f"SSH write file error: {e}")
            return False
        finally:
            os.unlink(local_path)

    async def close(self):
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            logger.debug("SSH connection closed")
        if self._temp_key_path and os.path.exists(self._temp_key_path):
            os.unlink(self._temp_key_path)