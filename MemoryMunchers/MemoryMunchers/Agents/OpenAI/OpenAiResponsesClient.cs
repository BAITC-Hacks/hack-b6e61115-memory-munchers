using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents.OpenAI;

public sealed class OpenAiResponsesClient(HttpClient httpClient, IOptions<OpenAiOptions> options) : IAgentModelClient
{
    public async Task<AgentModelResponse> RespondAsync(AgentModelRequest request, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(options.Value.ApiKey))
            throw new AgentException("provider_not_configured", "Set OpenAI__ApiKey before running an agent.", 503);

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
            max_output_tokens = options.Value.MaxOutputTokens,
            store = false,
            include = new[] { "reasoning.encrypted_content" }
        });

        try
        {
            using var response = await httpClient.SendAsync(httpRequest, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                // Provider error bodies can echo prompts or credentials; don't return them to API callers.
                throw new AgentException("provider_error",
                    $"The model provider returned HTTP {(int)response.StatusCode}.", 502);
            }

            using var document = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync(cancellationToken),
                cancellationToken: cancellationToken);
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
}
