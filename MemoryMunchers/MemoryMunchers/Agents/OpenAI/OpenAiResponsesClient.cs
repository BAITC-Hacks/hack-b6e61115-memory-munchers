using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents.OpenAI;

public sealed class OpenAiResponsesClient(HttpClient httpClient, IOptions<OpenAiOptions> options, AgentEventSink events) : IAgentModelClient
{
    public async Task<AgentModelResponse> RespondAsync(AgentModelRequest request, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(options.Value.ApiKey))
            throw new AgentException("provider_not_configured",
                "Set OpenAI:ApiKey in appsettings.json or the OpenAI__ApiKey environment variable before running an agent.", 503);

        using var httpRequest = new HttpRequestMessage(HttpMethod.Post, "https://api.openai.com/v1/responses");
        httpRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", options.Value.ApiKey);
        httpRequest.Content = JsonContent.Create(new
        {
            model = request.Model,
            instructions = request.Instructions,
            input = request.Input,
            tools = request.Tools.Select(tool => new
            {
                type = "function", name = tool.Name, description = tool.Description,
                parameters = tool.Parameters, strict = true
            }),
            parallel_tool_calls = false,
            max_output_tokens = request.MaxOutputTokens ?? options.Value.MaxOutputTokens,
            reasoning = new { effort = request.ReasoningEffort ?? options.Value.ReasoningEffort },
            stream = events.Write != null,
            store = false,
            include = new[] { "reasoning.encrypted_content" }
        });

        try
        {
            using var response = await httpClient.SendAsync(httpRequest, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                // Provider error bodies can echo prompts or credentials; don't return them to API callers.
                throw new AgentException("provider_error",
                    $"The model provider returned HTTP {(int)response.StatusCode}.", 502);
            }

            using var document = events.Write == null
                ? await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken: cancellationToken)
                : await ReadStreamAsync(await response.Content.ReadAsStreamAsync(cancellationToken), cancellationToken);
            var root = document.RootElement;
            if (root.GetProperty("status").GetString() != "completed")
                throw new AgentException("incomplete_model_response", "The model did not complete its response. It may have reached the output token limit.", 502);

            var items = root.GetProperty("output").EnumerateArray().Select(item => item.Clone()).ToArray();
            var calls = new List<AgentToolCall>();
            var text = new List<string>();
            foreach (var item in items)
            {
                switch (item.GetProperty("type").GetString())
                {
                    case "function_call":
                        calls.Add(new AgentToolCall(RequiredString(item, "call_id"), RequiredString(item, "name"),
                            RequiredString(item, "arguments")));
                        break;
                    case "message":
                        foreach (var content in item.GetProperty("content").EnumerateArray())
                        {
                            var type = content.GetProperty("type").GetString();
                            if (type == "output_text") text.Add(RequiredString(content, "text"));
                            if (type == "refusal") text.Add(RequiredString(content, "refusal"));
                        }
                        break;
                }
            }

            if (calls.Select(call => call.CallId).Distinct(StringComparer.Ordinal).Count() != calls.Count)
                throw new JsonException("Duplicate tool call IDs.");
            return new AgentModelResponse(items, string.Join("\n", text), calls);
        }
        catch (HttpRequestException exception)
        {
            throw new AgentException("provider_unavailable", "The model provider could not be reached.", 502, exception);
        }
        catch (Exception exception) when (exception is JsonException or KeyNotFoundException or InvalidOperationException)
        {
            throw new AgentException("invalid_model_response", "The model provider returned an invalid response.", 502, exception);
        }
    }

    private static string RequiredString(JsonElement element, string name) =>
        element.GetProperty(name).GetString() ?? throw new JsonException($"Missing '{name}'.");

    private async Task<JsonDocument> ReadStreamAsync(Stream stream, CancellationToken token)
    {
        using var reader = new StreamReader(stream);
        while (await reader.ReadLineAsync(token) is { } line)
        {
            if (!line.StartsWith("data: ", StringComparison.Ordinal) || line == "data: [DONE]") continue;
            using var item = JsonDocument.Parse(line[6..]);
            var root = item.RootElement;
            switch (root.GetProperty("type").GetString())
            {
                case "response.output_text.delta":
                    await events.PublishAsync("delta", new { text = RequiredString(root, "delta") }, token);
                    break;
                case "response.completed": return JsonDocument.Parse(root.GetProperty("response").GetRawText());
                case "response.failed":
                case "response.incomplete":
                case "error": throw new AgentException("provider_error", "Не удалось завершить ответ. Повторите запрос.", 502);
            }
        }
        throw new AgentException("incomplete_model_response", "Поток ответа прервался. Обновите диалог.", 502);
    }
}
