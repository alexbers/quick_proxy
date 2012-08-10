# Quick proxy #

Yet another TCP proxy. Very simple to use -- just edit config file and run.

Supports Linux, Freebsd and Windows; IPv4 and IPv6, Python 2.6, 2.7, 3.1, 3.2.
No external modules required. Not forks on each connection. Saves sessions in
files.

## Configuration example ##
    
    PROXYMAPS = {
        3128 : ("e1.ru", 80),
        1234 : ("127.0.0.1", 80),
        3456 : ("dc21:c7f:2012:6::10", 22),
        4433 : ("alexbers.dyndns.org", 22)
    }