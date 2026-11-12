# Examples

## Custom template

Create a directory for project templates:

```bash
export LATEX_SERVER_TEMPLATE_DIRS="$HOME/latex/templates"
```

A template file may declare metadata and placeholders:

```latex
---
name: paper
description: Article scaffold
variables:
  title: Untitled
  author: Anonymous
required: [title]
---
\documentclass{article}
\title{{{title}}}
\author{{{author}}}
\begin{document}
\maketitle
{{content}}
\end{document}
```

Then call create_from_template with:

```json
{
  "file_path": "papers/my-paper.tex",
  "template": "paper",
  "variables": {
    "title": "My Paper",
    "content": "Hello."
  }
}
```

## Multi-file project

Call analyze_project with the main file, then validate_project or
compile_project. Includes such as \input{sections/intro} and
\include{sections/methods} are resolved recursively inside the base directory.

## Client demo

See client_demo.py for a minimal stdio client that lists tools, creates a
document, validates it, and reports its word count.

```bash
python examples/client_demo.py
```

The demo adds src to PYTHONPATH so it works from a source checkout without an
editable install.
