#!/usr/bin/env bash
set -e

VIP="${1:-10.1.200.85}"
VKEY="${2}"

if [ -z "$VKEY" ]; then
  if [ -f "/home/ubuntu/aigateway/tests/demo_vkey.txt" ]; then
    VKEY=$(cat /home/ubuntu/aigateway/tests/demo_vkey.txt | tr -d "\r\n")
  elif [ -f "/tmp/demo_vkey.txt" ]; then
    VKEY=$(cat /tmp/demo_vkey.txt | tr -d "\r\n")
  fi
fi

if [ -z "$VKEY" ]; then
  echo "Error: Virtual key token not provided and not found in demo_vkey.txt"
  echo "Usage: $0 [VIP] [VKEY_TOKEN]"
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "=== 1. Testing Models Endpoint (GET http://${VIP}/v1/models) ==="
curl -s -i "http://${VIP}/v1/models" -H "Authorization: Bearer ${VKEY}"
echo ""

echo "=== 2. Testing Chat Completion (Google Gemini: gemini-flash-latest) ==="
curl -s -i "http://${VIP}/v1/chat/completions" \
  -H "Authorization: Bearer ${VKEY}" \
  -H "Content-Type: application/json" \
  -d @"${SCRIPT_DIR}/test-gemini.json"
echo ""

echo "=== 3. Testing Unauthorized Model Enforcement (Expect 403 Forbidden) ==="
curl -s -i "http://${VIP}/v1/chat/completions" \
  -H "Authorization: Bearer ${VKEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
echo ""

echo "=== 4. Testing Invalid Virtual Key Enforcement (Expect 401 Unauthorized) ==="
curl -s -i "http://${VIP}/v1/chat/completions" \
  -H "Authorization: Bearer f5vk_invalid_key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-flash-latest","messages":[{"role":"user","content":"hi"}]}'
echo ""
