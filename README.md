# hack-b6e61115-memory-munchers
Hackathon team repository for Memory Munchers

# Sample Skyline forecast UI

This is a dependency-free sample page for `GET /WeatherForecast`.

1. Start the API from the repository root with `dotnet run --project MemoryMunchers/MemoryMunchers --launch-profile http`.
2. In another terminal, serve this folder on port 5500 with `python -m http.server 5500 --directory frontend`.
3. Open `http://localhost:5500` in a browser.

## OpenAI agent access

The web API registers the agent workflow through `AddAgenticWorkflow` and calls the OpenAI Responses API. Configure the API key with the `OpenAI__ApiKey` environment variable (or .NET user secrets).

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
