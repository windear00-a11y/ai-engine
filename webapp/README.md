# Knowledge Engine Web App v1

> **Note:** Part of **KGHEER Core**'s repository. A read-only browser for the
> Knowledge Engine Public Contract v1.

## Architecture

```
Browser
  -> webapp/index.html (SPA)
  -> HTTP POST /v1/execute
  -> Contract v1
  -> Knowledge Engine
  -> Knowledge DB
```

The Web App is **only** a client of the existing HTTP API. It never accesses
the database directly, never imports Python internals, and never implements
knowledge logic.

## How It Works

The Web App is a single-file SPA (`index.html`) containing HTML, CSS, and
vanilla JavaScript. No build tools, no npm, no dependencies.

### Starting the HTTP Server

```bash
# Start the Knowledge Engine HTTP server
python -m http_server --port 8765
```

### Opening the Web App

Open `webapp/index.html` in a browser, or serve it:

```bash
# Option 1: Open directly in a browser
open webapp/index.html

# Option 2: Serve with any static file server
python -m http.server 3000 --directory webapp
```

### Configuring the API URL

By default, the Web App connects to the same origin. To point to a different
server:

```bash
# Via URL parameter
open "webapp/index.html?api=http://127.0.0.1:8765"

# Via environment (set before serving)
# The ?api= parameter is read at load time
```

## Features

| Feature | Contract Operation | Description |
|---------|-------------------|-------------|
| Search | `search` | Full-text search of the knowledge base |
| Node Details | `get` | View a node's name, type, description, relationships |
| Related | `related` | See nodes connected to the current node |
| Follow | `follow` | Traverse relationship edges with type filtering |
| Provenance | `provenance` | View source information for a node |
| Status | `inspect` | View knowledge statistics and server health |

## Screens

- **Search**: Prominent search box, results as cards with type badges
- **Node Details**: Full node view with relationships, actions, provenance
- **Status**: System statistics (node count, relationship count, source count)

## States

Every network operation shows:
- Loading indicator
- Empty state when no results
- Error messages with retry option

## Responsive Design

Works on phone, tablet, and desktop. Mobile-first CSS with breakpoints at 600px.

## Security

- Never accesses SQLite or the database directly
- Never imports Python internals
- No API keys in source code
- All knowledge access goes through HTTP
- Contract v1 error codes are mapped to human-readable messages

## Technology

- **HTML/CSS/JavaScript** (single file, no dependencies)
- **No build tools** required
- **No npm** or package manager needed
- **No backend** inside the frontend project

## Supported Contract Operations

All six Contract v1 operations are supported:
`search`, `get`, `related`, `follow`, `provenance`, `inspect`

Plus the transport-level `GET /health` endpoint for server status.
