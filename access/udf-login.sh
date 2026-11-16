#!/bin/bash

# --- Configuration ---
TARGET_FQDN="bb663359-ac56-4748-b15e-f29e9398304c.access.udf.f5.com"
REMOTE_USER="ubuntu"

# Define your key-value pairs (Alias -> Port)
# --- Mapping Logic ---
# This function acts as our "Key-Value" store for Bash 3.2 compatibility
get_port() {
    case "$1" in
        "controller")    echo 47008;;
        "worker1")       echo 47002;;
        "worker2")       echo 47003;;
        "worker3")       echo 47004;;
        "worker4")       echo 47005;;
        "client"|"mgmt-host") echo 47000;;
        "cnf-std")       echo 47006;;
        "big-ip")        echo 47007;;
        *)               echo ""   ;; # Return empty if not found
    esac
}

# --- Logic ---

# Function to show available aliases
show_usage() {
    echo "Usage: $0 [alias]"
    echo "Available hosts: mgmt-host, controller, worker1, worker2, worker3, worker4, remote-server, vyos, big-ip"
    exit 1
}

# --- Execution ---

# 1. Check if argument exists
if [ -z "$1" ]; then
    show_usage
fi

# 2. Lookup the port
SELECTED_ALIAS="$1"
PORT=$(get_port "$SELECTED_ALIAS")

# 3. Validate the lookup
if [ -z "$PORT" ]; then
    echo "Error: Alias '$SELECTED_ALIAS' is not configured."
    show_usage
fi

echo "Connecting to $SELECTED_ALIAS (Port $PORT) at $TARGET_FQDN..."

# 4. Launch SSH
shift
EXIT_CODE=0
if [ $# -gt 0 ]; then
    SSH_AUTH_SOCK= ssh -q -p "$PORT" "$REMOTE_USER@$TARGET_FQDN" \
        -i ./bkk_lab_keypair.pem \
        -o PubkeyAcceptedKeyTypes=+ssh-rsa \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 \
        "$@"
    EXIT_CODE=$?
    # udfssh2.0.0srv closes session without exit status, causing ssh client to return 255
    if [ $EXIT_CODE -eq 255 ]; then
        EXIT_CODE=0
    fi
else
    SSH_AUTH_SOCK= ssh -t -p "$PORT" "$REMOTE_USER@$TARGET_FQDN" \
        -i ./bkk_lab_keypair.pem \
        -o PubkeyAcceptedKeyTypes=+ssh-rsa \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10
    EXIT_CODE=$?
fi

# In UDF, the remote proxy banner or multiplexer might yield exit code 255 upon session end even after successful command output.
exit $EXIT_CODE
