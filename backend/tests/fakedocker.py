"""Faux démon Docker sur un socket Unix : juste assez de l'API pour tester les opérations, les routes et la liaison avec Traefik."""
import json
import os
import socketserver
import struct
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler


def frame(kind: int, text: str) -> bytes:
    b = text.encode()
    return struct.pack(">BxxxI", kind, len(b)) + b


class FakeDocker:
    def __init__(self, path: str) -> None:
        self.path = path
        self.calls: list[tuple[str, str]] = []
        self.containers = {
            "aaaaaaaaaaaa1111": {"Id": "aaaaaaaaaaaa1111", "Names": ["/web-1"], "Image": "nginx:alpine", "ImageID": "sha256:img1aaaaaaaaaa", "State": "running", "Status": "Up 3 days (healthy)", "Created": 1700000000,
                                 "Ports": [{"IP": "0.0.0.0", "PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}, {"IP": "::", "PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}],
                                 "Labels": {"com.docker.compose.project": "site", "com.docker.compose.service": "web", "com.docker.compose.project.working_dir": "/srv/site", "secret.label": "x"},
                                 "NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.5", "Aliases": ["web", "web-1"]}}},
                                 "Mounts": [{"Type": "volume", "Name": "site_data", "Source": "/var/lib/docker/volumes/site_data/_data", "Destination": "/data", "RW": True}, {"Type": "bind", "Name": "", "Source": "/srv/site/conf", "Destination": "/etc/site", "RW": False}]},
            "bbbbbbbbbbbb2222": {"Id": "bbbbbbbbbbbb2222", "Names": ["/blog"], "Image": "ghost:5", "ImageID": "sha256:img2bbbbbbbbbb", "State": "running", "Status": "Up 1 hour", "Created": 1700000100,
                                 "Ports": [], "Labels": {"traefik.http.routers.blog.rule": "Host(`blog.example.org`)", "traefik.http.services.blog.loadbalancer.server.port": "2368"},
                                 "NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.6", "Aliases": []}}}, "Mounts": []},
            "cccccccccccc3333": {"Id": "cccccccccccc3333", "Names": ["/db"], "Image": "postgres:16", "ImageID": "sha256:img3cccccccccc", "State": "exited", "Status": "Exited (0) 2 hours ago", "Created": 1700000200,
                                 "Ports": [], "Labels": {}, "NetworkSettings": {"Networks": {"backend": {"IPAddress": "", "Aliases": []}}}, "Mounts": []},
            "dddddddddddd4444": {"Id": "dddddddddddd4444", "Names": ["/traefik"], "Image": "traefik:v3.7", "ImageID": "sha256:img4dddddddddd", "State": "running", "Status": "Up 5 days", "Created": 1700000300,
                                 "Ports": [{"IP": "0.0.0.0", "PrivatePort": 443, "PublicPort": 443, "Type": "tcp"}], "Labels": {}, "Cmd": ["--providers.docker=true", "--providers.docker.exposedbydefault=false"],
                                 "NetworkSettings": {"Networks": {"proxy": {"IPAddress": "172.20.0.2", "Aliases": ["traefik"]}}}, "Mounts": []},
        }
        self.removed: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def address_string(self):
                return "unix"

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code=200):
                self._send(code, json.dumps(obj).encode())

            def _find(self, key):
                return next((c for c in outer.containers.values() if key in (c["Id"], c["Id"][:12], c["Names"][0].lstrip("/"))), None)

            def handle_any(self):
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                outer.calls.append((self.command, self.path))
                parts = [p for p in u.path.split("/") if p]
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                if len(parts) == 3 and parts[0] == "exec" and parts[2] == "start":    # flux « détourné » : écho de ce qu'on tape
                    self.wfile.write(b"HTTP/1.1 101 UPGRADED\r\nContent-Type: application/vnd.docker.raw-stream\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
                    self.wfile.flush()
                    self.close_connection = True
                    while True:
                        data = self.connection.recv(4096)
                        if not data:
                            return
                        self.wfile.write(b"echo:" + data)
                        self.wfile.flush()
                if len(parts) == 3 and parts[0] == "exec" and parts[2] == "resize":
                    return self._send(200)
                if u.path == "/version":
                    return self._json({"Version": "29.0.0", "ApiVersion": "1.52"})
                if u.path == "/info":
                    return self._json({"OperatingSystem": "Fake OS", "KernelVersion": "7.0", "Name": "fakehost", "Containers": 4, "ContainersRunning": 3, "Images": 3, "NCPU": 4, "MemTotal": 8 << 30})
                if parts[:2] == ["containers", "json"]:
                    return self._json([c for c in outer.containers.values() if q.get("all") == "1" or c["State"] == "running"])
                if parts and parts[0] == "containers" and len(parts) >= 2:
                    c = self._find(parts[1])
                    if parts[1] == "prune":
                        return self._json({"ContainersDeleted": ["x"], "SpaceReclaimed": 10})
                    if not c:
                        return self._json({"message": f"No such container: {parts[1]}"}, 404)
                    act = parts[2] if len(parts) > 2 else ""
                    if act == "exec" and self.command == "POST":
                        return self._json({"Id": "exec1"}, 201)
                    if self.command == "DELETE":
                        outer.removed.append(c["Names"][0].lstrip("/"))
                        del outer.containers[c["Id"]]
                        return self._send(204)
                    if act == "json":
                        return self._json({"Id": c["Id"], "Name": c["Names"][0], "Image": c["ImageID"], "Created": "2026-11-01T10:00:00Z",
                                           "Config": {"Image": c["Image"], "Cmd": c.get("Cmd", ["nginx", "-g", "daemon off;"]), "Entrypoint": [], "User": "", "WorkingDir": "/", "Tty": False,
                                                      "Env": ["SECRET=hunter2", "PATH=/bin"], "Labels": c["Labels"]},
                                           "State": {"Status": c["State"], "Running": c["State"] == "running", "StartedAt": "2026-11-10T10:00:00Z", "FinishedAt": "", "ExitCode": 0, "OOMKilled": False, "Health": {"Status": "healthy"}},
                                           "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "Memory": 0, "NanoCpus": 0, "NetworkMode": "proxy"}, "RestartCount": 0,
                                           "Mounts": c["Mounts"], "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
                                                                                       "Networks": {k: {"IPAddress": v["IPAddress"], "Gateway": "172.20.0.1", "Aliases": v["Aliases"]} for k, v in c["NetworkSettings"]["Networks"].items()}}})
                    if act in ("start", "stop", "restart") and self.command == "POST":
                        want = "exited" if act == "stop" else "running"
                        if act != "restart" and c["State"] == want:
                            return self._send(304)
                        c["State"] = want
                        return self._send(204)
                    if act == "logs":
                        lines = [("2026-11-14T15:37:47.100000000Z", "premier", 1), ("2026-11-14T15:37:48.200000000Z", "deuxième", 1), ("2026-11-14T15:37:49.300000000Z", "erreur", 2)]
                        since = float(q.get("since", 0))
                        body = b"".join(frame(k, f"{t} {m}\n") for t, m, k in lines)
                        return self._send(200, body, "application/vnd.docker.multiplexed-stream") if not since else self._send(200, b"")
                    if act == "stats":
                        return self._json({"cpu_stats": {"cpu_usage": {"total_usage": 2000}, "system_cpu_usage": 20000, "online_cpus": 2}, "precpu_stats": {"cpu_usage": {"total_usage": 1000}, "system_cpu_usage": 10000},
                                           "memory_stats": {"usage": 5000, "limit": 10000, "stats": {"inactive_file": 1000}}, "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}}})
                if u.path == "/images/json":
                    return self._json([{"Id": "sha256:img1aaaaaaaaaa", "RepoTags": ["nginx:alpine"], "Size": 50, "Created": 1, "RepoDigests": ["nginx@sha256:" + "a" * 64]},
                                       {"Id": "sha256:img9zzzzzzzzzz", "RepoTags": ["<none>:<none>"], "Size": 10, "Created": 2, "RepoDigests": []}])
                if parts and parts[0] == "images" and self.command == "DELETE":
                    outer.removed.append("image:" + parts[1])
                    return self._json([{"Deleted": parts[1]}])
                if u.path == "/images/prune":
                    return self._json({"ImagesDeleted": [{"Deleted": "a"}, {"Deleted": "b"}], "SpaceReclaimed": 123})
                if u.path == "/networks":
                    return self._json([{"Id": "n1" * 8, "Name": "bridge", "Driver": "bridge", "Scope": "local", "IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}},
                                       {"Id": "n2" * 8, "Name": "proxy", "Driver": "bridge", "Scope": "local", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}]}}])
                if parts and parts[0] == "networks" and self.command == "DELETE":
                    outer.removed.append("network:" + parts[1])
                    return self._send(204)
                if u.path == "/volumes":
                    return self._json({"Volumes": [{"Name": "site_data", "Driver": "local", "Mountpoint": "/x", "CreatedAt": "2026-11-01", "Labels": {"com.docker.compose.project": "site"}},
                                                   {"Name": "orphan", "Driver": "local", "Mountpoint": "/y", "CreatedAt": "2026-11-02", "Labels": None}]})
                if parts and parts[0] == "volumes" and self.command == "DELETE":
                    outer.removed.append("volume:" + parts[1])
                    return self._send(204)
                return self._json({"message": "not found: " + u.path}, 404)

            do_GET = do_POST = do_DELETE = handle_any

        class Srv(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        if os.path.exists(path):
            os.unlink(path)
        self.srv = Srv(path, H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
