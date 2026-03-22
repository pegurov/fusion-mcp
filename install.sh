#!/bin/bash
# Fusion MCP Bridge — installation script
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"

echo "=== Fusion MCP Bridge ==="
echo ""

# 1. Create venv and install dependencies
echo "[1/3] Setting up Python virtual environment..."
cd "$SERVER_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -q "mcp>=1.0.0"
deactivate
echo "  Done."

# 2. Create exchange directories
echo "[2/3] Creating exchange directories..."
mkdir -p "$SCRIPT_DIR/exchange/renders"
echo "  Done."

# 3. Register MCP server in Claude Code
echo "[3/3] Registering MCP server in Claude Code..."
claude mcp add --scope user fusion-mcp-bridge -- "$SERVER_DIR/venv/bin/python" "$SERVER_DIR/server.py"
echo "  Done."

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Open Fusion 360"
echo "  2. Go to UTILITIES > ADD-INS (or press Shift+S)"
echo "  3. Click the green '+' and select: $SCRIPT_DIR/addin"
echo "  4. Select FusionMCPBridge and click 'Run'"
echo "  5. Restart Claude Code to pick up the new MCP server"
echo ""
echo "Then ask Claude to draw something!"
