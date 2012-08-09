#!/usr/bin/env python

import socket
import errno
import threading
import time
from glob import glob
from select import select

from config import PROXYMAPS


class SocketPairs:
    def __init__(self):
        self.pairs = {}

    def add_pair(self, s1, s2):
        self.pairs[s1] = s2
        self.pairs[s2] = s1

    def get_pair(self, s):
        return self.pairs[s]

    def del_pair(self, s1, s2=None):
        if s2 is None:
            s2 = self.pairs[s1]

        del self.pairs[s1]
        del self.pairs[s2]


class Dumper:
    def __init__(self, port):
        last_session_num = len(glob("port%d_*.dmp" % port))
        self.filename = "port%d_%09d.dmp" % (port, last_session_num + 1)

    def dump(self, data):
        with open(self.filename, "ab") as f:
            f.write(data)


class Proxy(threading.Thread):
    def __init__(self,
                 listen_port, server_host, server_port,
                 listen_ipv6=True):

        threading.Thread.__init__(self, name='port' + str(listen_port))

        server_addrs = socket.getaddrinfo(server_host, server_port,
                                          0, socket.SOCK_STREAM)
        server_addr = server_addrs[0]
        if len(server_addrs) > 1:
            readable_addrs = [addr[4][0] for addr in server_addrs]
            print("host %s resolved into several addrs: %s. Using first: %s" %
                  (server_host, readable_addrs, server_addr[4][0]))

        self.listen_port = listen_port

        self.server_family   = server_addr[0]
        self.server_socktype = server_addr[1]
        self.server_proto    = server_addr[2]
        self.server_sockaddr = server_addr[4][:2]

        self.listen_ipv6     = listen_ipv6

        print("Proxy for port %s ready" % self.listen_port)

    def run(self):
        if self.listen_ipv6:
            proxy = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            proxy.bind(("::", self.listen_port))
        else:
            proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            proxy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            proxy.bind(("0.0.0.0", self.listen_port))

        want_read = []
        want_write = []

        data_to_send = {}
        client_sockets = set()
        server_sockets = set()
        closed_but_data_left_sockets = set()
        socket_pairs = SocketPairs()
        socket_to_dumper = {}

        proxy.listen(10)
        want_read.append(proxy)

        while True:
            # clean up the client sockets that are not want to read or write
            client_sockets &= set(want_read + want_write)
            server_sockets &= set(want_read + want_write)

            vaild_sockets = client_sockets | server_sockets
            # clean up closed socket-pairs
            for s in list(socket_pairs.pairs):
                if (s in socket_pairs.pairs and
                    s not in vaild_sockets and
                    socket_pairs.get_pair(s) in socket_pairs.pairs and
                    socket_pairs.get_pair(s) not in vaild_sockets):
                    socket_pairs.del_pair(s)

            # clean up unused datas to send
            vaild_sockets = socket_pairs.pairs
            for s in list(data_to_send):
                if s not in vaild_sockets:
                    del data_to_send[s]

            for s in list(socket_to_dumper):
                if s not in vaild_sockets:
                    del socket_to_dumper[s]

            try:
                ready_read, ready_write = select(want_read, want_write,
                                                 [], 10)[:2]
            except:
                # clean up bad want_read's and want_write's fd
                for s in list(want_read):
                    try:
                        select([s], [], [], 0)
                    except:
                        want_read.remove(s)

                for s in list(want_write):
                    try:
                        select([], [s], [], 0)
                    except:
                        want_write.remove(s)
                continue

            for s in ready_read:
                if s == proxy:  # there is a new connect to proxy
                    client, address = proxy.accept()
                    client.setblocking(0)
                    client_sockets.add(client)

                    server = socket.socket(self.server_family,
                                           self.server_socktype)
                    server.setblocking(0)
                    server_sockets.add(server)

                    data_to_send[client] = b''
                    data_to_send[server] = b''

                    socket_pairs.add_pair(client, server)

                    try:
                        server.connect(self.server_sockaddr)
                    except socket.error as E:
                        if E.errno == errno.EINPROGRESS or E.errno == 10035:
                            pass  # it is normal to have EINPROGRESS here
                        else:
                            client.close()
                            server.close()
                            continue

                    socket_to_dumper[server] = Dumper(self.listen_port)

                    want_read.append(server)
                    want_read.append(client)  # want an want_read from client

                    print("Connect port %s" % self.listen_port)

            for s in ready_read:
                if s != proxy:  # usual socket
                    s_pair = socket_pairs.get_pair(s)
                    try:
                        data = s.recv(4096)
                    except:
                        s_pair.close()
                        break

                    if data:
                        if s in server_sockets:
                            socket_to_dumper[s].dump(data)
                        data_to_send[s_pair] += data
                        want_write.append(s_pair)
                    else:  # connection was closed
                        if s in server_sockets:
                            client = s_pair

                            want_read.remove(s)
                            if client in want_read:
                                want_read.remove(client)

                            if data_to_send[client]:
                                closed_but_data_left_sockets.add(client)
                                want_write.append(client)
                                client.shutdown(socket.SHUT_RD)
                            else:
                                client.close()

                            s.close()
                        else:
                            server = s_pair

                            want_read.remove(s)
                            if server in want_read:
                                want_read.remove(server)

                            server.close()
                            s.close()
                        break

            for s in ready_write:
                if not data_to_send[s]:
                    if s in closed_but_data_left_sockets:
                        del data_to_send[s]
                        s.close()
                        closed_but_data_left_sockets.remove(s)

                    want_write.remove(s)
                    break

                try:
                    sent = s.send(data_to_send[s])
                except:
                    socket_pairs.get_pair(s).close()
                    break

                if s in server_sockets:
                    socket_to_dumper[s].dump(data_to_send[s][:sent])

                data_to_send[s] = data_to_send[s][sent:]

for listen_port, sockaddr in PROXYMAPS.items():
    server_host, server_port = sockaddr
    p = Proxy(listen_port, server_host, server_port)
    p.daemon = True
    p.start()

while True:
    time.sleep(1337)
