#!/bin/sh
# Prepares a new Oracle Cloud instance with Ubuntu (22.04 or 24.04, Ampere A1
# or x86) to run QQplay. Safe to run again:
#
#   1. opens ports 80 and 443 (TCP, and UDP for HTTP/3) and 1935 (RTMP) in the
#      instance's own firewall. Oracle's Ubuntu images block everything but
#      SSH with iptables, besides the security list of the cloud network;
#   2. installs Docker Engine with the compose plugin, from Docker's packages;
#   3. lets the current user run docker without sudo.
#
#   sh deploy/oracle/setup-host.sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    sudo=""
else
    sudo="sudo"
fi

rules=/etc/iptables/rules.v4
unsaved=""

open_port() {  # <tcp|udp> <port>
    rule="INPUT -p $1 -m state --state NEW -m $1 --dport $2 -j ACCEPT"
    # shellcheck disable=SC2086  # the rule is meant to split into arguments
    if ! $sudo iptables -C $rule 2>/dev/null; then
        # shellcheck disable=SC2086
        $sudo iptables -I $rule
        echo "opened $1/$2"
    fi
    # The same rule in the file restored at boot, right after the SSH one.
    # The file is edited instead of saved from the live rules, which would
    # also capture Docker's own rules.
    if [ -f "$rules" ]; then
        if ! $sudo grep -qF -- "-A $rule" "$rules"; then
            $sudo sed -i "/--dport 22 -j ACCEPT/a -A $rule" "$rules"
        fi
        if ! $sudo grep -qF -- "-A $rule" "$rules"; then
            unsaved="$unsaved $1/$2"
        fi
    fi
}

echo "== Firewall of the instance"
open_port tcp 80
open_port tcp 443
open_port udp 443
open_port tcp 1935
if [ ! -f "$rules" ]; then
    echo "note: $rules does not exist, so the ports are open until the next boot only."
    echo "If this machine restores firewall rules at boot, open them there too."
elif [ -n "$unsaved" ]; then
    echo "warning: could not add$unsaved to $rules (no SSH rule to follow)."
    echo "Add them by hand before the REJECT line, or they close at the next boot."
fi

echo "== Docker"
if command -v docker >/dev/null 2>&1; then
    echo "already installed: $(docker --version)"
else
    script=$(mktemp)
    curl -fsSL https://get.docker.com -o "$script"
    $sudo sh "$script"
    rm -f "$script"
fi
if ! $sudo docker compose version >/dev/null 2>&1; then
    $sudo apt-get update
    $sudo apt-get install -y docker-compose-plugin
fi
$sudo systemctl enable --now docker >/dev/null 2>&1 || true
$sudo docker compose version

user=${SUDO_USER:-$(id -un)}
if [ "$user" != "root" ] && ! id -nG "$user" | tr ' ' '\n' | grep -qx docker; then
    $sudo usermod -aG docker "$user"
    echo "$user may now use docker without sudo after logging in again."
    relogin=1
fi

echo
echo "Done. Also open TCP 80, 443 and 1935 (and UDP 443) in the security list"
echo "of the instance's subnet, in the Oracle Cloud console."
if [ "${relogin:-0}" = 1 ]; then
    echo "Log out and back in (exit, then ssh again) before the next step."
fi
echo "Next:  sh deploy/configure.sh <domain> <e-mail>"
