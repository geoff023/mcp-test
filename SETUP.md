# Setup — Jira, the gateway, and connecting MCP

Do these in order. Steps 1–3 are browser work you must do yourself. From step 5 onward you
can hand the work to Claude Code.

Total time if nothing goes wrong: about an hour.

---

## 1. Create a Jira Cloud site and a project

1. Go to <https://www.atlassian.com/software/jira> and sign up for a **free** Jira Cloud
   site. You will get a URL like `https://yourname.atlassian.net`.
2. Create a project:
   - Template: **Scrum**
   - Type: **Company-managed** (choose this, not team-managed — the REST API is more
     predictable and sprints behave normally)
   - Key: something short, e.g. `AUR`
3. Open **Board → Backlog** and confirm you can see a backlog and a sprint.
4. Turn on estimation in story points: **Project settings → Features → Estimation**, set to
   Story Points if it is not already.

You do not need a paid plan, and you do not need Rovo. We are using the plain REST API,
which is available on the free tier.

---

## 2. Create an API token

1. Go to <https://id.atlassian.com/manage-profile/security/api-tokens>
2. **Create API token**, name it `agentic-pm-fyp`, copy it immediately — it is shown once.
3. Paste it somewhere safe for the next step.

This token has the same permissions as your account. Treat it like a password: never commit
it, never paste it into a chat, never put it in `.mcp.json`.

---

## 3. Prove it works before writing any code

In a terminal:

```bash
export JIRA_BASE_URL="https://yourname.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_API_TOKEN="paste-token-here"

# 1. Who am I? Should return your account JSON.
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Accept: application/json" \
  "$JIRA_BASE_URL/rest/api/3/myself" | head -c 400

# 2. Can I see my project?
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Accept: application/json" \
  "$JIRA_BASE_URL/rest/api/3/project/AUR" | head -c 400
```

If both return JSON, the entire architecture is viable. **Screenshot this** — it is the
evidence for your supervisor that the approach works.

If you get `401`, the email or token is wrong. If you get `404` on the project, check the
project key.

---

## 4. Find the story points field ID

Story points are a custom field whose ID differs between sites. Find yours:

```bash
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Accept: application/json" \
  "$JIRA_BASE_URL/rest/api/3/field" \
  | python3 -c "import sys,json; [print(f['id'], '|', f['name']) for f in json.load(sys.stdin) if 'story' in f['name'].lower() or 'point' in f['name'].lower()]"
```

You will get something like `customfield_10016 | Story Points`. Write that ID down — it goes
in `.env` in step 6.

---

## 5. Create the repo

```bash
mkdir agentic-pm && cd agentic-pm
git init
mkdir -p gateway app db scripts docs tests
```

Put `CLAUDE.md` at the root and `slice-1.md` in `docs/`.

Create `.gitignore` **before your first commit**:

```
.env
*.db
__pycache__/
.venv/
.pytest_cache/
```

Commit that first, on its own. It is the one commit that protects you from leaking a token.

---

## 6. Environment file

Create `.env` in the repo root:

```
JIRA_BASE_URL=https://yourname.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=paste-token-here
JIRA_PROJECT_KEY=AUR
JIRA_STORY_POINTS_FIELD=customfield_10016
DB_PATH=./db/agentic_pm.db
```

Also create `.env.example` with the same keys and empty values, and **do** commit that one.
It documents what is needed without leaking anything.

---

## 7. Python environment

```bash
# install uv if you don't have it: https://docs.astral.sh/uv/
uv init
uv add "mcp[cli]" httpx python-dotenv fastapi uvicorn jinja2
uv add --dev pytest
```

---

## 8. Connect the MCP server to Claude Code

This is the part people get wrong, so do it exactly.

### 8a. Project-scoped registration (recommended)

Create `.mcp.json` in the repo root. It is committed to git, so it must contain **no
secrets** — the server reads those from `.env` itself.

```json
{
  "mcpServers": {
    "agentic-pm": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "${CLAUDE_PROJECT_DIR}",
        "run",
        "python",
        "-m",
        "gateway.server"
      ]
    }
  }
}
```

`${CLAUDE_PROJECT_DIR}` expands to your repo root, which avoids the most common failure —
the server being started from an unexpected working directory and not finding `.env`.

The first time you use it, Claude Code will ask you to approve the project-scoped server.
Approve it once.

### 8b. Or register from the CLI

Equivalent, if you prefer not to hand-write JSON:

```bash
claude mcp add --scope project agentic-pm -- uv run python -m gateway.server
```

The `--` is mandatory. Everything after it is the command to run your server.

To pass an environment variable at registration time (not needed here, since we use `.env`):

```bash
claude mcp add --scope project --env LOG_LEVEL=error agentic-pm -- uv run python -m gateway.server
```

**Scopes:** `--scope local` is you-only on this machine, `--scope project` writes
`.mcp.json` and is shared with your teammates via git, `--scope user` applies across all your
projects. Use `project` — your FYP partners will need it too.

### 8c. Check the connection

```bash
claude mcp list          # shows every configured server and whether it connected
claude mcp get agentic-pm   # details and the actual error if it failed
claude mcp remove agentic-pm  # if you need to start over
```

Inside a Claude Code session:

```
/mcp
```

This opens a panel showing status per server. You want `✔ Connected`, and selecting the
server should list your tools. Status meanings you may see:

| Status | Meaning |
|---|---|
| `✔ Connected` | working |
| `! Connected · tools fetch failed` | server started but tool listing threw — run `claude mcp get agentic-pm` |
| `⏸ Pending approval` | project-scoped server not yet trusted; run `claude` and approve |
| `✘ Failed to connect` | process didn't start or crashed — see troubleshooting below |

### 8d. Claude Desktop (optional)

If you also want the gateway available in the Claude desktop app, add the same block to:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Same `mcpServers` JSON shape, but use an **absolute path** instead of
`${CLAUDE_PROJECT_DIR}`. Quit the app completely from the menu bar and reopen it — closing
the window is not enough.

---

## 9. Troubleshooting a stdio MCP server

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to connect` | Something in `gateway/` printed to **stdout**. stdout *is* the protocol channel. | Remove every `print()`. Log to stderr or a file. |
| `Failed to connect` | Relative path in `command` | Use `uv`/`python` from PATH, or `${CLAUDE_PROJECT_DIR}/...` |
| Server can't find `.env` | Started from a different working directory | Use `--directory ${CLAUDE_PROJECT_DIR}` as shown above |
| Connected but no tools | Server crashed after startup, or a required env var is missing | Run the command by hand in the terminal and read the traceback |
| Timeout on first run | Dependency install on first launch | `MCP_TIMEOUT=60000 claude` |
| Env var appears literally as `${FOO}` | Not set in your shell | Set it, or give a default: `${FOO:-fallback}` |

**Fastest debugging move:** run the server manually.

```bash
uv run python -m gateway.server
```

If it exits or prints a traceback, fix that before touching MCP config. Nine times out of
ten the MCP layer is fine and the server is broken.

---

## 10. A note on Atlassian's own MCP server

Atlassian publishes a hosted "Rovo MCP server". **We are not using it**, for two reasons:

1. It exposes a fixed tool set with no concept of our autonomy levels. We cannot make write
   tools disappear at L1, which is the central mechanism of this project.
2. It draws on Rovo credits, which are bundled only with paid Atlassian plans.

You may connect it separately for *exploration* — seeing what a vendor MCP server exposes is
useful background for the report — but it is not part of the build.

---

## 11. Checklist before your first Claude Code session

- [ ] Jira site exists, Scrum project created, story points enabled
- [ ] API token created and stored safely
- [ ] `curl /rest/api/3/myself` returns JSON (screenshot saved)
- [ ] Story points custom field ID found
- [ ] Repo initialised, `.gitignore` committed **first**
- [ ] `.env` filled in, `.env.example` committed
- [ ] `CLAUDE.md` at repo root, `docs/slice-1.md` in place
- [ ] `uv` dependencies installed

Then open Claude Code in the repo and start with the prompt at the end of `docs/slice-1.md`.
