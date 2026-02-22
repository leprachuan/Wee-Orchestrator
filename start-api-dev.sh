#!/bin/bash
cd /opt/n8n-copilot-shim-dev
export $(cat .env | xargs)
exec python3 agent_manager.py --api
