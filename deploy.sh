#!/bin/bash

# Deployment setup script for referral contest bot
echo "🚀 Setting up Referral Contest Bot for deployment..."

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements-deploy.txt

# Create systemd service file (for VPS deployment)
echo "⚙️ Creating systemd service..."
cat > /etc/systemd/system/refbot.service << EOF
[Unit]
Description=Referral Contest Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/ref-contest-bot
Environment=PATH=/home/ubuntu/ref-contest-bot/venv/bin
ExecStart=/home/ubuntu/ref-contest-bot/venv/bin/python complete_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
systemctl daemon-reload
systemctl enable refbot.service
systemctl start refbot.service

echo "✅ Bot deployed and running!"
echo "📊 Check status with: systemctl status refbot.service"
echo "📝 View logs with: journalctl -u refbot.service -f"
