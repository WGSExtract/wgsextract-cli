# Job servers

Some actions are better run as jobs on a long-running server than as one process per click:

- the server reports structured progress;
- it can queue work;
- it cancels a job cleanly.

A bundle can declare such a server in `manifest.json`. Its actions can then run as jobs on it. Each action keeps its `command`, which runs whenever the server can't be used.

```jsonc
// manifest.json
"jobServer": {
  "command": {
    "executable": "{{bundleRoot}}/scripts/run-tool.sh",
    "arguments": ["--parent-pid", "{{appPid}}", "serve", "--ready-json"]
  },
  "startupTimeoutSeconds": 60
}

// an action in a page file
{
  "id": "coverage",
  "title": "Coverage",
  "command": { "executable": "{{bundleRoot}}/scripts/run-tool.sh", "arguments": ["coverage", "--input", "{{bam_path}}"] },
  "serverOperation": {
    "operation": "info/calculate-coverage",
    "parameters": { "input": "{{bam_path}}", "bins": true, "outdir": "{{out_dir}}" }
  }
}
```

**`parameters`** are sent as the JSON request body.

- String values are interpolated like command arguments.
- A string that ends up empty is left out.
- Numbers, booleans and other values are sent as written.

**`{{bundleRoot}}`, `{{home}}` and `{{appPid}}`** are the only placeholders available to `jobServer.command`. `{{appPid}}` is the app's process ID. Pass it to a server that can watch it, so the server exits when the app dies, even when the app is killed and can't stop it.

## Lifecycle

**Startup.** The app starts the server the first time an action with a `serverOperation` runs. It runs in the bundle workspace, like actions do.

**Ready line.** Once the server is listening, it must print one line of JSON on stdout with its base URL and a bearer token: `{"url": "http://127.0.0.1:PORT/v1", "token": "..."}`. Other lines before it are ignored. A server that can't start may print `{"error": {"message": "..."}}` instead; the app logs that it refused to start, with the message, and falls back.

Locally launched job servers must advertise a numeric loopback URL, such as
`127.0.0.1` or `::1`. The app rejects external destinations and hostnames before
sending bearer credentials or sample parameters. Requests refuse redirects,
including redirects to another local service. WGS Extract's bundled command
pins `--listen 127.0.0.1 --port 0`, overriding any saved server listen port or
public URL. Explicit remote viewer connections use a separate consented path;
they do not redirect ordinary jobs.

**Restarts.** If the server exits, the next action starts it again. It stops with the app and when another bundle is loaded, like every other process the app started.

**Fallback.** The app runs the action's `command` instead when:

- the server can't be started;
- it prints no ready line within `startupTimeoutSeconds`;
- the connection is refused before the app can send the request;
- the server explicitly rejects the submission before creating a job.

If the reply is lost or doesn't identify the job, the app checks the original job ID.
It never submits again or runs the command directly in this case. If it cannot
recover the job, it reports the ID so the user can check the server before trying again.

## Protocol

Every request carries `Authorization: Bearer <token>`. Paths are relative to `url`.

| Request | Response |
| --- | --- |
| `POST /ops/{operation}` with the parameters as a JSON body and `X-WGSE-Job-ID` set to a client-generated lowercase UUIDv4, plus `?card=1` for an action with `resultCard` | 2xx with a job: `{"id": "...", "status": "..."}`. The `id` matches the request header. |
| `GET /jobs/{id}/events` | Server-sent events until the job has finished, then the stream ends. `status` events carry `{"status": ...}`; `progress` events carry `stage`, `message` and/or `fraction` (0–1). |
| `GET /jobs/{id}` | The job; `exit_code` once it has finished, and `error` if it failed. The action's exit code is `exit_code`, else 0 for `succeeded` and 1 otherwise. When the bundle declares a [result document](result-documents.md) for the action, it is read from `result`. |
| `GET /jobs/{id}/log?tail=0` | The job's whole log as text; it is added to the action's output. |
| `DELETE /jobs/{id}` | Cancels the job. 409 means it had already finished. |

When the user cancels the action, the app sends `DELETE` and follows the job's events until it has finished, for up to a minute.

[WGSExtract CLI's `wgsextract serve`](../../docs/server.md) implements this protocol (`serve --ready-json`). The WGSExtract example bundle uses it for its coverage actions; with a bundled CLI older than that flag, they run their commands.
