# hack-b6e61115-memory-munchers
Hackathon team repository for Memory Munchers

# Sample Skyline forecast UI

This is a dependency-free sample page for `GET /WeatherForecast`.

1. Start the API from the repository root with `dotnet run --project MemoryMunchers/MemoryMunchers --launch-profile http`.
2. In another terminal, serve this folder on port 5500 with `python -m http.server 5500 --directory frontend`.
3. Open `http://localhost:5500` in a browser.

## OpenAI agent access

The `MemoryMunchers/AgenticAccess` library provides `IChatGptClient`, registered by the web API through `AddAgenticAccess`. Configure the API key with the `OpenAI__ApiKey` environment variable (or .NET user secrets), and optionally set `OpenAI__Model`.

Inject `IChatGptClient` into a service or controller and call `GenerateAsync(new ChatGptRequest("Your prompt"))`. To continue a response-linked conversation, pass the previous response's `Id` as `PreviousResponseId`.
