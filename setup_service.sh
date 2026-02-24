#!/bin/bash
# setup_service.sh — Create and enable systemd service for Börsihai bot

# Ensure we're in the project root
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)

# Load environment variables (to get SYSTEMD_SERVICE_NAME)
if [ -f .env ]; then
  source .env
fi

# Fallback service name if not defined in .env
SERVICE_NAME=${SYSTEMD_SERVICE_NAME:-borsihai}
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
USER_NAME=$(whoami)

echo "Setting up systemd service: ${SERVICE_NAME}"
echo "Project Directory: ${PROJECT_DIR}"
echo "User: ${USER_NAME}"

# Create the service file content
read -r -d '' SERVICE_CONTENT << EOM
[Unit]
Description=Borsihai Crypto Trading Bot
After=network.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/run.sh
Restart=on-failure
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOM

# Write the service file
echo "$SERVICE_CONTENT" | sudo tee "$SERVICE_FILE" > /dev/null

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling and starting ${SERVICE_NAME} service..."
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo "Service setup complete!"
echo "Check status directly with: sudo systemctl status ${SERVICE_NAME}"
echo "Check logs with: journalctl -u ${SERVICE_NAME} -f"
echo "Or use the /restart telegram command."
