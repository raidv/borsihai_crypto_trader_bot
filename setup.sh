#!/bin/bash

echo "Starting Börsihai 2026 Crypto Assistant Setup..."

# 1. Update system and install Python 3.12 if not present
if ! command -v python3.12 &> /dev/null
then
    echo "Python 3.12 not found. Installing..."
    sudo apt update
    sudo apt install software-properties-common -y
    sudo add-apt-repository ppa:deadsnakes/ppa -y
    sudo apt update
    sudo apt install python3.12 python3.12-venv -y
else
    echo "Python 3.12 is already installed."
fi

# 2. Create and activate a Virtual Environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment 'venv'..."
    python3.12 -m venv venv
else
    echo "Virtual environment 'venv' already exists."
fi

source venv/bin/activate

# 3. Install requirements
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo "====================================="
echo "Setup complete!"
echo ""
echo "Before running, ensure you have exported your telegram token:"
echo "export TELEGRAM_TOKEN='your_token_here'"
echo ""
echo "To start the bot, simply run:"
echo "./run.sh"
echo "====================================="
