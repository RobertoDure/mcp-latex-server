# MCP LaTeX Server

A comprehensive Model Context Protocol (MCP) server that provides advanced LaTeX file creation, editing, and management capabilities for Large Language Models and AI assistants.

## Overview

The MCP LaTeX Server enables AI assistants like Claude to seamlessly work with LaTeX documents through a standardized protocol. It provides tools for creating, editing, reading, and validating LaTeX files, making it easy to generate professional academic papers, reports, presentations, and other LaTeX documents.

## Features

- 📝 **Create LaTeX Documents**: Generate new LaTeX files from templates or custom specifications
- ✏️ **Edit Existing Files**: Modify LaTeX documents with various edit operations
- 📖 **Read File Contents**: Access and analyze LaTeX file content
- 📁 **File Management**: List and organize LaTeX files in directories
- ✅ **Syntax Validation**: Basic LaTeX syntax checking and error detection
- 🎯 **Document Types**: Support for article, report, book, letter, beamer, and minimal document classes
- 📦 **Package Management**: Automatic handling of LaTeX packages and dependencies
- 🔧 **Template System**: Built-in templates for common document types

## Prerequisites

Before installing the MCP LaTeX Server, ensure you have:

- **Python 3.8+** installed on your system
- **LaTeX Distribution** (recommended for full functionality):
  - **Windows**: MiKTeX or TeX Live
  - **macOS**: MacTeX
  - **Linux**: TeX Live
- **VS Code** (for VS Code integration)
- **Claude Desktop** or **Claude in VS Code** (for Claude integration)

### Installing LaTeX Distribution

#### Windows (MiKTeX)
1. Download MiKTeX from [https://miktex.org/download](https://miktex.org/download)
2. Run the installer and follow the setup wizard
3. Choose "Install packages on-the-fly" for automatic package management

#### Windows (TeX Live)
1. Download TeX Live from [https://www.tug.org/texlive/](https://www.tug.org/texlive/)
2. Run the installer (this may take some time as it's a large distribution)

#### macOS
```bash
# Using Homebrew
brew install --cask mactex

# Or download directly from https://www.tug.org/mactex/
```

#### Linux (Ubuntu/Debian)
```bash
sudo apt-get update
sudo apt-get install texlive-full
```

#### Linux (Fedora/RHEL)
```bash
sudo dnf install texlive-scheme-full
```

## Installation

### Method 1: Quick Setup (Recommended)

1. **Clone or download** the repository:
```bash
git clone <repository-url>
cd mcp-latex-server
```

2. **Run the setup script**:
```bash
python setup.py
```

This will automatically install all required dependencies.

### Method 2: Manual Installation

1. **Create a virtual environment** (recommended):
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Verify installation**:
```bash
python latex_server.py --help
```

### Method 3: Using uv (Fast Python Package Manager)

1. **Install uv** (if not already installed):
```bash
pip install uv
```

2. **Install dependencies with uv**:
```bash
uv pip install -r requirements.txt
```

## Configuration

### Basic Configuration

The server can be configured to work with specific directories for LaTeX files. By default, it allows access to the current working directory and subdirectories.

### Environment Variables

You can set the following environment variables:

```bash
# Set the base path for LaTeX files
export LATEX_BASE_PATH="/path/to/your/latex/files"

# Set logging level
export LATEX_LOG_LEVEL="INFO"  # DEBUG, INFO, WARNING, ERROR
```

### Configuration File

Create a `config.json` file in the server directory:

```json
{
  "base_path": "/path/to/your/latex/files",
  "allowed_extensions": [".tex", ".sty", ".cls", ".bib"],
  "max_file_size": "10MB",
  "enable_validation": true,
  "default_document_class": "article",
  "default_packages": [
    "amsmath",
    "amsfonts",
    "amssymb",
    "graphicx",
    "hyperref"
  ]
}
```

## MCP Integration

### Claude Desktop Integration

1. **Locate Claude Desktop Configuration**:
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`

2. **Add MCP Server Configuration**:

```json
{
  "mcpServers": {
    "latex-server": {
      "command": "python",
      "args": [
        "C:\\path\\to\\mcp-latex-server\\latex_server.py"
      ],
      "env": {
        "LATEX_BASE_PATH": "C:\\path\\to\\your\\latex\\files"
      },
      "cwd": "C:\\path\\to\\mcp-latex-server"
    }
  }
}
```

For **uv** (recommended for better performance):

```json
{
  "mcpServers": {
    "latex-server": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\path\\to\\mcp-latex-server",
        "run",
        "latex_server.py"
      ],
      "env": {
        "LATEX_BASE_PATH": "C:\\path\\to\\your\\latex\\files"
      },
      "cwd": "C:\\path\\to\\mcp-latex-server"
    }
  }
}
```

3. **Restart Claude Desktop** for changes to take effect.

### VS Code Integration with Claude

1. **Install the Claude extension** in VS Code

2. **Configure MCP Server** in VS Code settings:

Open VS Code settings (`Ctrl+,` or `Cmd+,`) and add:

```json
{
  "claude.mcpServers": {
    "latex-server": {
      "command": "python",
      "args": ["C:\\path\\to\\mcp-latex-server\\latex_server.py"],
      "cwd": "C:\\path\\to\\mcp-latex-server",
      "env": {
        "LATEX_BASE_PATH": "${workspaceFolder}"
      }
    }
  }
}
```

3. **Alternative: Using tasks.json**

Create `.vscode/tasks.json` in your workspace:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Start LaTeX MCP Server",
      "type": "shell",
      "command": "python",
      "args": ["C:\\path\\to\\mcp-latex-server\\latex_server.py", "--base-path", "${workspaceFolder}"],
      "group": "build",
      "presentation": {
        "echo": true,
        "reveal": "always",
        "focus": false,
        "panel": "new"
      },
      "isBackground": true,
      "problemMatcher": []
    }
  ]
}
```

### Testing the Integration

1. **Start Claude Desktop** or **Claude in VS Code**

2. **Test the connection** by asking Claude:
   ```
   Can you list the available LaTeX tools?
   ```

3. **Create a test document**:
   ```
   Please create a simple LaTeX article with the title "Test Document" and some sample content.
   ```

## Usage Examples

### Basic Document Creation

```python
# Example interaction with Claude
"Create a LaTeX article titled 'My Research Paper' with author 'John Doe' and include sections for Introduction, Methodology, Results, and Conclusion."
```

### Advanced Document Creation

```python
# Creating a complex document
{
  "name": "create_latex_file",
  "arguments": {
    "file_path": "research_paper.tex",
    "document_type": "article",
    "title": "Advanced Machine Learning Techniques",
    "author": "Jane Smith",
    "date": "\\today",
    "packages": ["amsmath", "amsfonts", "graphicx", "booktabs", "hyperref"],
    "geometry": "margin=1in",
    "content": "\\section{Introduction}\nThis paper explores...\n\n\\section{Methodology}\nWe employed..."
  }
}
```

### Editing Operations

```python
# Replace content
{
  "name": "edit_latex_file",
  "arguments": {
    "file_path": "document.tex",
    "operation": "replace",
    "search_text": "\\section{Old Section}",
    "new_text": "\\section{New Section}"
  }
}

# Insert content
{
  "name": "edit_latex_file",
  "arguments": {
    "file_path": "document.tex",
    "operation": "insert_after",
    "search_text": "\\section{Introduction}",
    "new_text": "\nThis section provides an overview of the research topic."
  }
}

# Append content
{
  "name": "edit_latex_file",
  "arguments": {
    "file_path": "document.tex",
    "operation": "append",
    "new_text": "\n\\section{Conclusion}\nIn conclusion, this study demonstrates..."
  }
}
```

## Available Tools

### 1. `create_latex_file`
Creates a new LaTeX document with specified parameters.

**Parameters:**
- `file_path` (required): Path where the file should be created
- `document_type`: Document class (article, report, book, letter, beamer, minimal)
- `title`: Document title
- `author`: Document author
- `date`: Document date (default: \today)
- `packages`: List of LaTeX packages to include
- `geometry`: Page geometry settings
- `content`: Main document content

### 2. `edit_latex_file`
Edits an existing LaTeX file with various operations.

**Parameters:**
- `file_path` (required): Path to the file to edit
- `operation` (required): Type of edit (replace, insert_before, insert_after, append, prepend)
- `new_text` (required): New text to insert or replace with
- `search_text`: Text to search for (required for replace, insert_before, insert_after)
- `line_number`: Line number for insertion (alternative to search_text)

### 3. `read_latex_file`
Reads and returns the contents of a LaTeX file.

**Parameters:**
- `file_path` (required): Path to the file to read

### 4. `list_latex_files`
Lists all LaTeX files in a directory.

**Parameters:**
- `directory_path`: Directory to search (default: current directory)
- `recursive`: Whether to search subdirectories (default: false)

### 5. `validate_latex`
Performs basic LaTeX syntax validation.

**Parameters:**
- `file_path` (required): Path to the file to validate

### 6. `get_latex_structure`
Extracts the structure of a LaTeX document (sections, subsections, etc.).

**Parameters:**
- `file_path` (required): Path to the file to analyze

## Troubleshooting

### Common Issues

#### 1. Server Won't Start
```bash
# Check Python version
python --version  # Should be 3.8+

# Check dependencies
pip list | grep mcp

# Run with debug logging
python latex_server.py --log-level DEBUG
```

#### 2. Claude Can't Connect to Server
- Verify the file paths in your configuration are correct
- Check that Python is in your system PATH
- Restart Claude Desktop after configuration changes
- Check Claude Desktop logs for error messages

#### 3. Permission Errors
```bash
# Windows: Run as administrator if needed
# macOS/Linux: Check file permissions
chmod +x latex_server.py
```

#### 4. LaTeX Compilation Issues
- Ensure LaTeX distribution is properly installed
- Check that required packages are available
- Use the validation tool to check syntax

### Debug Mode

Run the server in debug mode for detailed logging:

```bash
python latex_server.py --log-level DEBUG --base-path /your/latex/path
```

### Log Files

Check log files for troubleshooting:
- Server logs: Available in console output
- Claude Desktop logs: Check Claude's log directory
- VS Code logs: Check VS Code's output panel

## Advanced Configuration

### Custom Templates

Create custom templates in the `templates/` directory:

```latex
% templates/custom_article.tex
\documentclass[12pt]{article}
\usepackage{your_custom_packages}

\title{{{TITLE}}}
\author{{{AUTHOR}}}
\date{{{DATE}}}

\begin{document}
\maketitle

{{{CONTENT}}}

\end{document}
```

### Security Considerations

- The server only allows access to specified directories
- File operations are limited to LaTeX-related extensions
- Input validation prevents malicious LaTeX code execution

### Performance Optimization

- Use SSD storage for LaTeX files
- Keep LaTeX distribution up to date
- Use `uv` instead of `pip` for faster dependency management

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Update documentation
5. Submit a pull request

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Support

For support and questions:
- Check the troubleshooting section above
- Review GitHub issues
- Create a new issue with detailed information about your problem

## Changelog

### Version 1.0.0
- Initial release
- Basic LaTeX file operations
- MCP integration support
- Template system
