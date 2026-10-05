#!/usr/bin/env bash
# Pair + connect + install Project B in ONE shot, so the code cannot time out
# between me reading it and using it.
#
# Usage: bash pair-and-install.sh <PAIRING_CODE>
#
# Why one script: the pairing dialog on Android closes after a short idle window.
# Last attempt died because the code was entered through a prompt that took 7
# minutes to return. Reading the code and pairing must happen together.
set -u
A="$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"
A="$HOME/AppData/Local/Android/Sdk/platform-tools/adb.exe"
CODE="${1:?usage: pair-and-install.sh <PAIRING_CODE>}"
APK="$HOME/PROJECT B/android/app/build/outputs/apk/debug/app-debug.apk"

echo "=== 1. fresh mDNS scan (ports rotate every time the dialog opens) ==="
SVC=$("$A" mdns services 2>&1 | tr -d '\r')
echo "$SVC"
# The line is TAB-separated: name <TAB> service <TAB> ip:port
# `awk '{print $3}'` is WRONG here -- it splits on runs of whitespace and misses.
PAIR=$(printf '%s\n' "$SVC" | grep -i "_adb-tls-pairing" | cut -f3 | head -1)
CONN=$(printf '%s\n' "$SVC" | grep -i "_adb-tls-connect" | cut -f3 | head -1)
echo "  pairing endpoint: ${PAIR:-(absent)}"
echo "  connect endpoint: ${CONN:-(absent)}"

if [ -z "$PAIR" ]; then
  echo "!! no pairing service advertised -- the dialog is not open. Reopen it."
  exit 2
fi

echo
echo "=== 2. pair to $PAIR ==="
"$A" pair "$PAIR" "$CODE" 2>&1 | head -4

echo
echo "=== 3. connect ==="
if [ -n "$CONN" ]; then
  "$A" connect "$CONN" 2>&1 | head -3
fi
# a fresh scan: the connect port can change once pairing succeeds
sleep 2
CONN2=$("$A" mdns services 2>&1 | grep -i "_adb-tls-connect" | awk '{print $3}' | head -1)
[ -n "$CONN2" ] && [ "$CONN2" != "$CONN" ] && { echo "  connect port moved -> $CONN2"; "$A" connect "$CONN2" 2>&1 | head -3; }

echo
echo "=== 4. devices ==="
"$A" devices -l 2>&1 | grep -v "^List" | grep -v "^$"

if "$A" devices | grep -q "device$"; then
  echo
  echo "=== 5. INSTALL (debug variant preserves settings/token/upload queue) ==="
  "$A" install -r "$APK" 2>&1 | tail -6
  echo
  echo "=== 6. verified: is it installed, and what version? ==="
  "$A" shell dumpsys package com.example.project-b 2>&1 | grep -E "versionName|versionCode|firstInstallTime|lastUpdateTime" | head -5
  echo
  echo "=== 7. does the app now have a pending upload queue? ==="
  "$A" shell run-as com.example.project-b ls -la files/recordings 2>&1 | head -10 || echo "(run-as unavailable on a release-signed build)"
else
  echo
  echo "!! still no device -- pairing did not complete. Get a NEW code and rerun."
  exit 1
fi