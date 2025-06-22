#!/usr/bin/env python3
"""
MCP LaTeX Server - A Model Context Protocol server for LaTeX file creation and editing.
"""

import argparse
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel
)
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LaTeXServer:
    """MCP Server for LaTeX file operations."""
    
    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path).resolve()
        self.server = Server("latex-server")
        self._setup_tools()
        self._setup_resources()
    
    def _setup_tools(self):
        """Setup MCP tools for LaTeX operations."""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [
                Tool(
                    name="create_latex_file",
                    description="Create a new LaTeX document with specified content and structure",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path where the LaTeX file should be created"
                            },
                            "document_type": {
                                "type": "string",
                                "enum": ["article", "report", "book", "letter", "beamer", "minimal"],
                                "description": "Type of LaTeX document",
                                "default": "article"
                            },
                            "title": {
                                "type": "string",
                                "description": "Document title",
                                "default": ""
                            },
                            "author": {
                                "type": "string",
                                "description": "Document author",
                                "default": ""
                            },
                            "date": {
                                "type": "string",
                                "description": "Document date (use \\today for current date)",
                                "default": "\\today"
                            },
                            "content": {
                                "type": "string",
                                "description": "Main content of the document",
                                "default": ""
                            },
                            "packages": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of LaTeX packages to include",
                                "default": []
                            },
                            "geometry": {
                                "type": "string",
                                "description": "Page geometry settings (e.g., 'margin=1in')",
                                "default": ""
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="edit_latex_file",
                    description="Edit an existing LaTeX file by replacing content or inserting new content",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the LaTeX file to edit"
                            },
                            "operation": {
                                "type": "string",
                                "enum": ["replace", "insert_before", "insert_after", "append", "prepend"],
                                "description": "Type of edit operation"
                            },
                            "search_text": {
                                "type": "string",
                                "description": "Text to search for (required for replace, insert_before, insert_after)"
                            },
                            "new_text": {
                                "type": "string",
                                "description": "New text to insert or replace with"
                            },
                            "line_number": {
                                "type": "integer",
                                "description": "Line number for insertion (alternative to search_text)"
                            }
                        },
                        "required": ["file_path", "operation", "new_text"]
                    }
                ),
                Tool(
                    name="read_latex_file",
                    description="Read and return the contents of a LaTeX file",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the LaTeX file to read"
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="list_latex_files",
                    description="List all LaTeX files in a directory",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "directory_path": {
                                "type": "string",
                                "description": "Directory path to search for LaTeX files",
                                "default": "."
                            },
                            "recursive": {
                                "type": "boolean",
                                "description": "Whether to search recursively in subdirectories",
                                "default": False
                            }
                        }
                    }
                ),
                Tool(
                    name="validate_latex",
                    description="Perform basic LaTeX syntax validation on a file",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the LaTeX file to validate"
                            }
                        },
                        "required": ["file_path"]
                    }
                ),
                Tool(
                    name="get_latex_structure",
                    description="Extract the structure of a LaTeX document (sections, subsections, etc.)",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Path to the LaTeX file to analyze"
                            }
                        },
                        "required": ["file_path"]
                    }
                )
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            try:
                if name == "create_latex_file":
                    return await self._create_latex_file(**arguments)
                elif name == "edit_latex_file":
                    return await self._edit_latex_file(**arguments)
                elif name == "read_latex_file":
                    return await self._read_latex_file(**arguments)
                elif name == "list_latex_files":
                    return await self._list_latex_files(**arguments)
                elif name == "validate_latex":
                    return await self._validate_latex(**arguments)
                elif name == "get_latex_structure":
                    return await self._get_latex_structure(**arguments)
                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as e:
                logger.error(f"Error in tool {name}: {str(e)}")
                return [TextContent(type="text", text=f"Error: {str(e)}")]
    
    def _setup_resources(self):
        """Setup MCP resources."""
        
        @self.server.list_resources()
        async def list_resources() -> List[Resource]:
            """List available LaTeX files as resources."""
            resources = []
            try:
                for tex_file in self.base_path.rglob("*.tex"):
                    if tex_file.is_file():
                        relative_path = tex_file.relative_to(self.base_path)                        
                        resources.append(
                            Resource(
                                uri=f"file://{tex_file}",
                                name=str(relative_path),
                                description=f"LaTeX file: {relative_path}",
                                mimeType="text/x-tex"
                            )
                        )
            except Exception as e:
                logger.error(f"Error listing resources: {e}")
            
            return resources
        
        @self.server.read_resource()
        async def read_resource(uri: str) -> str:
            """Read a LaTeX file resource."""
            try:
                if uri.startswith("file://"):
                    file_path = Path(uri[7:])
                    if file_path.suffix == ".tex" and file_path.exists():
                        return file_path.read_text(encoding="utf-8")
                    else:
                        raise ValueError(f"File not found or not a LaTeX file: {file_path}")
                else:
                    raise ValueError(f"Unsupported URI scheme: {uri}")
            except Exception as e:
                logger.error(f"Error reading resource {uri}: {e}")
                raise
    
    def _get_file_path(self, file_path: str) -> Path:
        """Get absolute file path, ensuring it's within the base path."""
        path = Path(file_path)
        if not path.is_absolute():
            path = self.base_path / path
        
        # Ensure the path is within a reasonable scope for security
        # Allow access to the parent directory of base_path (Codes directory)
        allowed_base = self.base_path.parent.resolve()
        try:
            path.resolve().relative_to(allowed_base)
        except ValueError:
            raise ValueError(f"Path {file_path} is outside the allowed base path ({allowed_base})")
        
        return path
    
    def _create_latex_template(self, document_type: str, title: str, author: str, date: str,
                             content: str, packages: List[str], geometry: str) -> str:
        """Create a LaTeX document template."""
        template_parts = []
        
        # Document class
        template_parts.append(f"\\documentclass{{{document_type}}}")
        
        # Packages
        default_packages = ["inputenc", "fontenc", "babel"]
        if geometry:
            template_parts.append(f"\\usepackage[{geometry}]{{geometry}}")
        
        for package in default_packages + packages:
            if package == "inputenc":
                template_parts.append("\\usepackage[utf8]{inputenc}")
            elif package == "fontenc":
                template_parts.append("\\usepackage[T1]{fontenc}")
            elif package == "babel":
                template_parts.append("\\usepackage[english]{babel}")
            else:
                template_parts.append(f"\\usepackage{{{package}}}")
        
        # Title, author, date
        if title:
            template_parts.append(f"\\title{{{title}}}")
        if author:
            template_parts.append(f"\\author{{{author}}}")
        if date:
            template_parts.append(f"\\date{{{date}}}")
        
        # Begin document
        template_parts.append("\\begin{document}")
        
        # Make title if title is provided
        if title:
            template_parts.append("\\maketitle")
        
        # Content
        if content:
            template_parts.append(content)
        else:
            template_parts.append("% Your content here")
        
        # End document
        template_parts.append("\\end{document}")
        
        return "\n\n".join(template_parts)
    
    async def _create_latex_file(self, file_path: str, document_type: str = "article",
                               title: str = "", author: str = "", date: str = "\\today",
                               content: str = "", packages: List[str] = None,
                               geometry: str = "") -> List[TextContent]:
        """Create a new LaTeX file."""
        if packages is None:
            packages = []
        
        try:
            path = self._get_file_path(file_path)
            
            # Create directory if it doesn't exist
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Generate LaTeX content
            latex_content = self._create_latex_template(
                document_type, title, author, date, content, packages, geometry
            )
            
            # Write file
            path.write_text(latex_content, encoding="utf-8")
            
            return [TextContent(
                type="text",
                text=f"Successfully created LaTeX file: {path}\n\nContent:\n{latex_content}"
            )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error creating LaTeX file: {str(e)}")]
    
    async def _edit_latex_file(self, file_path: str, operation: str, new_text: str,
                             search_text: str = None, line_number: int = None) -> List[TextContent]:
        """Edit an existing LaTeX file."""
        try:
            path = self._get_file_path(file_path)
            
            if not path.exists():
                return [TextContent(type="text", text=f"File not found: {path}")]
            
            # Read current content
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            
            if operation == "replace":
                if not search_text:
                    return [TextContent(type="text", text="search_text is required for replace operation")]
                content = content.replace(search_text, new_text)
            
            elif operation == "insert_before":
                if search_text:
                    content = content.replace(search_text, f"{new_text}\n{search_text}")
                elif line_number is not None and 1 <= line_number <= len(lines):
                    lines.insert(line_number - 1, new_text)
                    content = "\n".join(lines)
                else:
                    return [TextContent(type="text", text="search_text or valid line_number is required")]
            
            elif operation == "insert_after":
                if search_text:
                    content = content.replace(search_text, f"{search_text}\n{new_text}")
                elif line_number is not None and 1 <= line_number <= len(lines):
                    lines.insert(line_number, new_text)
                    content = "\n".join(lines)
                else:
                    return [TextContent(type="text", text="search_text or valid line_number is required")]
            
            elif operation == "append":
                content = content + "\n" + new_text
            
            elif operation == "prepend":
                content = new_text + "\n" + content
            
            else:
                return [TextContent(type="text", text=f"Unknown operation: {operation}")]
            
            # Write updated content
            path.write_text(content, encoding="utf-8")
            
            return [TextContent(
                type="text",
                text=f"Successfully edited LaTeX file: {path}\n\nOperation: {operation}\nFile updated."
            )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error editing LaTeX file: {str(e)}")]
    
    async def _read_latex_file(self, file_path: str) -> List[TextContent]:
        """Read and return the contents of a LaTeX file."""
        try:
            path = self._get_file_path(file_path)
            
            if not path.exists():
                return [TextContent(type="text", text=f"File not found: {path}")]
            
            content = path.read_text(encoding="utf-8")
            
            return [TextContent(
                type="text",
                text=f"Contents of {path}:\n\n{content}"
            )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading LaTeX file: {str(e)}")]
    
    async def _list_latex_files(self, directory_path: str = ".", recursive: bool = False) -> List[TextContent]:
        """List all LaTeX files in a directory."""
        try:
            dir_path = self._get_file_path(directory_path)
            
            if not dir_path.exists() or not dir_path.is_dir():
                return [TextContent(type="text", text=f"Directory not found: {dir_path}")]
            
            pattern = "**/*.tex" if recursive else "*.tex"
            tex_files = list(dir_path.glob(pattern))
            
            if not tex_files:
                return [TextContent(type="text", text=f"No LaTeX files found in {dir_path}")]
            
            file_list = []
            for tex_file in sorted(tex_files):
                relative_path = tex_file.relative_to(self.base_path)
                file_stats = tex_file.stat()
                size = file_stats.st_size
                modified = file_stats.st_mtime
                
                file_list.append(f"  {relative_path} ({size} bytes)")
            
            return [TextContent(
                type="text",
                text=f"LaTeX files in {dir_path}:\n\n" + "\n".join(file_list)
            )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error listing LaTeX files: {str(e)}")]
    
    async def _validate_latex(self, file_path: str) -> List[TextContent]:
        """Perform basic LaTeX syntax validation."""
        try:
            path = self._get_file_path(file_path)
            
            if not path.exists():
                return [TextContent(type="text", text=f"File not found: {path}")]
            
            content = path.read_text(encoding="utf-8")
            issues = []
            
            # Check for basic LaTeX structure
            if "\\documentclass" not in content:
                issues.append("Missing \\documentclass declaration")
            
            if "\\begin{document}" not in content:
                issues.append("Missing \\begin{document}")
            
            if "\\end{document}" not in content:
                issues.append("Missing \\end{document}")
            
            # Check for balanced braces
            open_braces = content.count("{")
            close_braces = content.count("}")
            if open_braces != close_braces:
                issues.append(f"Unbalanced braces: {open_braces} opening, {close_braces} closing")
            
            # Check for common LaTeX environments
            begin_pattern = re.compile(r'\\begin\{([^}]+)\}')
            end_pattern = re.compile(r'\\end\{([^}]+)\}')
            
            begins = begin_pattern.findall(content)
            ends = end_pattern.findall(content)
            
            for env in begins:
                if begins.count(env) != ends.count(env):
                    issues.append(f"Unmatched environment: {env}")
            
            # Check for undefined references
            ref_pattern = re.compile(r'\\ref\{([^}]+)\}')
            label_pattern = re.compile(r'\\label\{([^}]+)\}')
            
            refs = set(ref_pattern.findall(content))
            labels = set(label_pattern.findall(content))
            
            undefined_refs = refs - labels
            if undefined_refs:
                issues.append(f"Undefined references: {', '.join(undefined_refs)}")
            
            if issues:
                return [TextContent(
                    type="text",
                    text=f"LaTeX validation issues found in {path}:\n\n" + "\n".join(f"- {issue}" for issue in issues)
                )]
            else:
                return [TextContent(
                    type="text",
                    text=f"LaTeX file {path} appears to be valid (basic checks passed)"
                )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error validating LaTeX file: {str(e)}")]
    
    async def _get_latex_structure(self, file_path: str) -> List[TextContent]:
        """Extract the structure of a LaTeX document."""
        try:
            path = self._get_file_path(file_path)
            
            if not path.exists():
                return [TextContent(type="text", text=f"File not found: {path}")]
            
            content = path.read_text(encoding="utf-8")
            structure = []
            
            # Extract document class
            doc_class_match = re.search(r'\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}', content)
            if doc_class_match:
                structure.append(f"Document class: {doc_class_match.group(1)}")
            
            # Extract title, author, date
            title_match = re.search(r'\\title\{([^}]+)\}', content)
            if title_match:
                structure.append(f"Title: {title_match.group(1)}")
            
            author_match = re.search(r'\\author\{([^}]+)\}', content)
            if author_match:
                structure.append(f"Author: {author_match.group(1)}")
            
            # Extract sections
            section_patterns = [
                (r'\\part\{([^}]+)\}', "Part"),
                (r'\\chapter\{([^}]+)\}', "Chapter"),
                (r'\\section\{([^}]+)\}', "Section"),
                (r'\\subsection\{([^}]+)\}', "Subsection"),
                (r'\\subsubsection\{([^}]+)\}', "Subsubsection"),
                (r'\\paragraph\{([^}]+)\}', "Paragraph"),
                (r'\\subparagraph\{([^}]+)\}', "Subparagraph"),
            ]
            
            structure.append("\nDocument structure:")
            for pattern, level in section_patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    structure.append(f"  {level}: {match.group(1)}")
            
            # Extract packages
            package_pattern = re.compile(r'\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}')
            packages = package_pattern.findall(content)
            if packages:
                structure.append(f"\nPackages used: {', '.join(set(packages))}")
            
            return [TextContent(
                type="text",
                text=f"Structure of {path}:\n\n" + "\n".join(structure)
            )]
        
        except Exception as e:
            return [TextContent(type="text", text=f"Error analyzing LaTeX structure: {str(e)}")]
    
    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="MCP LaTeX Server")
    parser.add_argument(
        "--base-path",
        type=str,
        default=".",
        help="Base path for LaTeX files (default: current directory)"
    )
    
    args = parser.parse_args()
    
    # Create and run the server
    server = LaTeXServer(base_path=args.base_path)
    
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()
