from sshtunnel import SSHTunnelForwarder
from config import DatabaseConfig

print("Connecting SSH tunnel...")
tunnel = SSHTunnelForwarder(
    (DatabaseConfig.ssh_host, DatabaseConfig.ssh_port),
    ssh_username=DatabaseConfig.ssh_user,
    ssh_pkey=DatabaseConfig.ssh_key_path,
    ssh_private_key_password=DatabaseConfig.ssh_key_passphrase,
    remote_bind_address=(DatabaseConfig.host, DatabaseConfig.port),
)
tunnel.start()
print(f"Tunnel open: localhost:{tunnel.local_bind_port}")
tunnel.stop()
print("Tunnel closed.")
