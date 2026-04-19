#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL_PREFIX="${LABEL_PREFIX:-com.$(id -un).briefly}"
WEB_LABEL="${LABEL_PREFIX}.web"
SCHEDULER_LABEL="${LABEL_PREFIX}.scheduler"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
WEB_PLIST="${LAUNCH_AGENTS_DIR}/${WEB_LABEL}.plist"
SCHEDULER_PLIST="${LAUNCH_AGENTS_DIR}/${SCHEDULER_LABEL}.plist"
LOG_DIR="${PROJECT_ROOT}/logs"
WEB_OUT_LOG="${LOG_DIR}/launchd-web.out.log"
WEB_ERR_LOG="${LOG_DIR}/launchd-web.err.log"
SCHEDULER_OUT_LOG="${LOG_DIR}/launchd-scheduler.out.log"
SCHEDULER_ERR_LOG="${LOG_DIR}/launchd-scheduler.err.log"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
GUI_DOMAIN="gui/$(id -u)"

usage() {
  cat <<EOF
Usage: scripts/service.sh <command>

Commands:
  install     Create launchd plists for web + scheduler.
  start       Load and start both launchd services.
  stop        Stop and unload both launchd services.
  restart     Restart both launchd services.
  status      Show state for both launchd services.
  logs        Tail logs for both services.
  logs-web    Tail web service logs only.
  logs-scheduler
              Tail scheduler logs only.
EOF
}

require_venv() {
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Missing virtualenv python at ${PYTHON_BIN}"
    echo "Create it with:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/pip install -e \".[all]\""
    exit 1
  fi
}

install_plists() {
  require_venv
  mkdir -p "${LAUNCH_AGENTS_DIR}" "${LOG_DIR}"

  cat > "${WEB_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${WEB_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>-m</string>
    <string>app.cli</string>
    <string>web</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8080</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${WEB_OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${WEB_ERR_LOG}</string>
</dict>
</plist>
EOF

  cat > "${SCHEDULER_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SCHEDULER_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>-m</string>
    <string>app.cli</string>
    <string>scheduler</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${SCHEDULER_OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${SCHEDULER_ERR_LOG}</string>
</dict>
</plist>
EOF

  echo "Installed:"
  echo "  ${WEB_PLIST}"
  echo "  ${SCHEDULER_PLIST}"
}

start_services() {
  launchctl bootout "${GUI_DOMAIN}" "${WEB_PLIST}" >/dev/null 2>&1 || true
  launchctl bootout "${GUI_DOMAIN}" "${SCHEDULER_PLIST}" >/dev/null 2>&1 || true
  launchctl bootstrap "${GUI_DOMAIN}" "${WEB_PLIST}"
  launchctl bootstrap "${GUI_DOMAIN}" "${SCHEDULER_PLIST}"
  launchctl enable "${GUI_DOMAIN}/${WEB_LABEL}"
  launchctl enable "${GUI_DOMAIN}/${SCHEDULER_LABEL}"
  launchctl kickstart -k "${GUI_DOMAIN}/${WEB_LABEL}"
  launchctl kickstart -k "${GUI_DOMAIN}/${SCHEDULER_LABEL}"
  echo "Started ${WEB_LABEL} and ${SCHEDULER_LABEL}"
}

stop_services() {
  launchctl bootout "${GUI_DOMAIN}" "${WEB_PLIST}" >/dev/null 2>&1 || true
  launchctl bootout "${GUI_DOMAIN}" "${SCHEDULER_PLIST}" >/dev/null 2>&1 || true
  echo "Stopped ${WEB_LABEL} and ${SCHEDULER_LABEL}"
}

status_services() {
  echo "== ${WEB_LABEL} =="
  launchctl print "${GUI_DOMAIN}/${WEB_LABEL}" | rg -n "state =|pid =|last exit code" || true
  echo
  echo "== ${SCHEDULER_LABEL} =="
  launchctl print "${GUI_DOMAIN}/${SCHEDULER_LABEL}" | rg -n "state =|pid =|last exit code" || true
}

logs_web() {
  mkdir -p "${LOG_DIR}"
  touch "${WEB_OUT_LOG}" "${WEB_ERR_LOG}"
  tail -f "${WEB_OUT_LOG}" "${WEB_ERR_LOG}"
}

logs_scheduler() {
  mkdir -p "${LOG_DIR}"
  touch "${SCHEDULER_OUT_LOG}" "${SCHEDULER_ERR_LOG}"
  tail -f "${SCHEDULER_OUT_LOG}" "${SCHEDULER_ERR_LOG}"
}

case "${1:-}" in
  install)
    install_plists
    ;;
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    stop_services
    start_services
    ;;
  status)
    status_services
    ;;
  logs)
    mkdir -p "${LOG_DIR}"
    touch "${WEB_OUT_LOG}" "${WEB_ERR_LOG}" "${SCHEDULER_OUT_LOG}" "${SCHEDULER_ERR_LOG}"
    tail -f "${WEB_OUT_LOG}" "${WEB_ERR_LOG}" "${SCHEDULER_OUT_LOG}" "${SCHEDULER_ERR_LOG}"
    ;;
  logs-web)
    logs_web
    ;;
  logs-scheduler)
    logs_scheduler
    ;;
  *)
    usage
    exit 1
    ;;
esac
