# hack-b6e61115-memory-munchers
Hackathon team repository for Memory Munchers

# Sample Skyline forecast UI

This is a dependency-free sample page for `GET /WeatherForecast`.

1. Start the API from the repository root with `dotnet run --project MemoryMunchers/MemoryMunchers --launch-profile http`.
2. In another terminal, serve this folder on port 5500 with `python -m http.server 5500 --directory frontend`.
3. Open `http://localhost:5500` in a browser.

## Agent chat UI

Open `http://localhost:5500/agents.html`, or choose **Agent chat** on the forecast page. The page uses the same static server as the forecast UI and defaults to the API's HTTP launch profile at `http://localhost:5187`. Expand **API connection** to change this address; it is remembered in your browser. For the HTTPS launch profile, use `https://localhost:7147` and trust the development certificate with `dotnet dev-certs https --trust`.

Choose an agent and start a conversation, optionally supplying a title and additional instructions. Send messages with Enter (Shift + Enter adds a new line), reopen saved conversations from the sidebar, and use **Load more** to see older sessions. Replies appear when the agent finishes; **Refresh chat** reloads saved messages and the latest run status.

The page calls `GET /api/agents`, `POST /api/agent-sessions`, `GET /api/agent-sessions?skip=…&take=…`, `GET /api/agent-sessions/{sessionId}`, and `POST /api/agent-sessions/{sessionId}/runs`. Run the API with its configured PostgreSQL database available and set the API key described below to receive agent replies.

## OpenAI agent access

The web API registers the agent workflow through `AddAgenticWorkflow` and calls the OpenAI Responses API. Create an API key using the [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), then set `OpenAI:ApiKey` in `MemoryMunchers/MemoryMunchers/appsettings.json`. Replace the empty `ApiKey` value in the existing `OpenAI` section, keeping its other settings:

```json
"ApiKey": "your-api-key-here"
```

Restart the API after setting the key. Keep the real key out of commits and frontend code. The `OpenAI__ApiKey` environment variable is still supported and overrides the value in `appsettings.json`.

If the key is empty, agent runs return HTTP 503 with `provider_not_configured` and setup instructions. A generic HTTP 500 response does not identify the cause; check the API's server logs for the underlying exception and the response's `traceId`.

Defaults in `MemoryMunchers/MemoryMunchers/appsettings.json` use [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) with its maximum output budget and reasoning effort:

| Setting | Default | Limit source |
| --- | --- | --- |
| `OpenAI:Model` | `gpt-6-sol` | OpenAI model ID |
| `OpenAI:MaxOutputTokens` | `128000` | Model maximum, including reasoning and visible output |
| `OpenAI:ReasoningEffort` | `max` | Highest supported reasoning effort |
| `OpenAI:RequestTimeoutSeconds` | `600` | Application maximum (10 minutes per request) |
| `Agents:RunTimeoutSeconds` | `1800` | Application maximum (30 minutes per run) |
| `Agents:Definitions:0:MaxModelCalls` | `32` | Application maximum per run |

Override these settings with environment variables using double underscores, such as `OpenAI__MaxOutputTokens` or `Agents__Definitions__0__MaxModelCalls`. The runner also permits up to 32 tool calls per run. The timeouts and call counts are application limits; API rate limits still depend on your OpenAI usage tier.

Create a session with `POST /api/agent-sessions`, then send messages to `POST /api/agent-sessions/{sessionId}/runs`. Sessions save their model and model-call limit when created, so create a new session to use the updated defaults. If overriding the model, configure output and reasoning settings supported by that model.
