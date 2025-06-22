#!/usr/bin/env python3
"""
Quick setup script for MCP LaTeX Server
This script helps users configure the MCP LaTeX Server for Claude Desktop and VS Code.
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

def get_claude_config_path():
    """Get the Claude Desktop configuration file path based on the operating system."""
    system = platform.system()
    
    if system == "Windows":
        return Path(os.getenv("APPDATA")) / "Claude" / "claude_desktop_config.json"
    elif system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Linux":
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    else:
        raise ValueError(f"Unsupported operating system: {system}")

def check_requirements():
    """Check if all requirements are met."""
    print("🔍 Checking requirements...")
    
    # Check Python version
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ is required")
        return False
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} is installed")
    
    # Check if uv is available
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        print("✅ uv is available (recommended)")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️ uv is not available, will use pip")
        return True

def install_dependencies():
    """Install required dependencies."""
    print("📦 Installing dependencies...")
    
    try:
        # Try to use uv first
        try:
            subprocess.run(["uv", "pip", "install", "-r", "requirements.txt"], check=True)
            print("✅ Dependencies installed with uv")
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to pip
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
            print("✅ Dependencies installed with pip")
        
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False

def create_claude_config():
    """Create or update Claude Desktop configuration."""
    print("⚙️ Configuring Claude Desktop...")
    
    try:
        config_path = get_claude_config_path()
        server_path = Path(__file__).parent.absolute()
        
        # Create configuration directory if it doesn't exist
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Read existing configuration or create new one
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
        else:
            config = {"mcpServers": {}}
        
        # Add LaTeX server configuration
        config["mcpServers"]["latex-server"] = {
            "command": "uv",
            "args": [
                "--directory",
                str(server_path),
                "run",
                "latex_server.py"
            ],
            "env": {
                "LATEX_BASE_PATH": str(server_path),
                "LATEX_LOG_LEVEL": "INFO"
            },
            "cwd": str(server_path)
        }
        
        # Write configuration
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"✅ Claude Desktop configuration updated: {config_path}")
        print("🔄 Please restart Claude Desktop for changes to take effect")
        return True
        
    except Exception as e:
        print(f"❌ Failed to configure Claude Desktop: {e}")
        return False

def create_vscode_config():
    """Create VS Code configuration examples."""
    print("📝 Creating VS Code configuration examples...")
    
    try:
        server_path = Path(__file__).parent.absolute()
        
        # Create .vscode directory
        vscode_dir = Path(".vscode")
        vscode_dir.mkdir(exist_ok=True)
        
        # Create tasks.json
        tasks_config = {
            "version": "2.0.0",
            "tasks": [
                {
                    "label": "Start LaTeX MCP Server",
                    "type": "shell",
                    "command": "python",
                    "args": [
                        str(server_path / "latex_server.py"),
                        "--base-path",
                        "${workspaceFolder}"
                    ],
                    "group": "build",
                    "presentation": {
                        "echo": True,
                        "reveal": "always",
                        "focus": False,
                        "panel": "new"
                    },
                    "isBackground": True,
                    "problemMatcher": []
                }
            ]
        }
        
        with open(vscode_dir / "tasks.json", 'w') as f:
            json.dump(tasks_config, f, indent=2)
        
        # Create launch.json
        launch_config = {
            "version": "0.2.0",
            "configurations": [
                {
                    "name": "LaTeX MCP Server",
                    "type": "python",
                    "request": "launch",
                    "program": str(server_path / "latex_server.py"),
                    "args": [
                        "--base-path",
                        "${workspaceFolder}"
                    ],
                    "console": "integratedTerminal",
                    "cwd": "${workspaceFolder}",
                    "env": {
                        "LATEX_BASE_PATH": "${workspaceFolder}"
                    }
                }
            ]
        }
        
        with open(vscode_dir / "launch.json", 'w') as f:
            json.dump(launch_config, f, indent=2)
        
        print("✅ VS Code configuration files created in .vscode/")
        return True
        
    except Exception as e:
        print(f"❌ Failed to create VS Code configuration: {e}")
        return False

def test_server():
    """Test if the server starts correctly."""
    print("🧪 Testing server startup...")
    
    try:
        result = subprocess.run([
            sys.executable, "latex_server.py", "--help"
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ Server starts correctly")
            return True
        else:
            print(f"❌ Server test failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ Server test timed out")
        return False
    except Exception as e:
        print(f"❌ Server test failed: {e}")
        return False

def main():
    """Main setup function."""
    print("🚀 MCP LaTeX Server Setup")
    print("=" * 50)
    
    # Change to script directory
    os.chdir(Path(__file__).parent)
    
    success = True
    
    # Check requirements
    if not check_requirements():
        success = False
    
    # Install dependencies
    if success and not install_dependencies():
        success = False
    
    # Test server
    if success and not test_server():
        success = False
    
    # Configure Claude Desktop
    if success:
        try:
            create_claude_config()
        except Exception as e:
            print(f"⚠️ Could not configure Claude Desktop: {e}")
    
    # Create VS Code configuration
    if success:
        try:
            create_vscode_config()
        except Exception as e:
            print(f"⚠️ Could not create VS Code configuration: {e}")
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 Setup completed successfully!")
        print("\nNext steps:")
        print("1. Restart Claude Desktop if you're using it")
        print("2. Test the integration by asking Claude about LaTeX tools")
        print("3. Check the README.md for detailed usage instructions")
        print("4. Review mcp_config_examples.md for additional configuration options")
    else:
        print("❌ Setup encountered some issues")
        print("Please check the error messages above and try again")
        print("You can also refer to the README.md for manual setup instructions")

if __name__ == "__main__":
    main()
