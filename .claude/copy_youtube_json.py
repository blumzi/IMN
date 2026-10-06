"""Copy ~/.config/IMN/youtube.json from one station to others, 0600, without printing it.

Usage: python .claude/copy_youtube_json.py <from-station> <to-station> [...]
Stations and passwords come from the same table as fleet_ssh.py.
"""
import hashlib, os, sys
import paramiko

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_ssh

SOURCE, TARGETS = sys.argv[1], sys.argv[2:]
stations = {s[1]: s for s in fleet_ssh.read_stations(fleet_ssh.STATIONS_FILE)}


def connect(name):
    ip, _station, user, password = stations[name]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username=user, password=password, timeout=10,
              allow_agent=False, look_for_keys=False)
    return c


src = connect(SOURCE)
with src.open_sftp() as sftp, sftp.open(".config/IMN/youtube.json", "rb") as f:
    data = f.read()
src.close()
digest = hashlib.sha256(data).hexdigest()[:12]
print("read {} bytes from {} (sha256 {})".format(len(data), SOURCE, digest))

for name in TARGETS:
    c = connect(name)
    try:
        _, o, e = c.exec_command("mkdir -p ~/.config/IMN && umask 077 && cat > ~/.config/IMN/youtube.json "
                                 "&& chmod 600 ~/.config/IMN/youtube.json && sha256sum ~/.config/IMN/youtube.json")
        o.channel.sendall(data)
        o.channel.shutdown_write()
        out = o.read().decode().split()
        rc = o.channel.recv_exit_status()
        ok = rc == 0 and out and out[0][:12] == digest
        print("{}: {}".format(name, "ok, checksum matches" if ok else "FAILED rc={} {}".format(rc, e.read().decode())))
    finally:
        c.close()
