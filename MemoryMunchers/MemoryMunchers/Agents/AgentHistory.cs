using System.Text.Json;

namespace MemoryMunchers.Agents;

internal static class AgentHistory
{
    public static JsonElement UserMessage(string message) => JsonSerializer.SerializeToElement(new
    {
        role = "user", content = message
    });

    public static JsonElement ToolOutput(string callId, string output) => JsonSerializer.SerializeToElement(new
    {
        type = "function_call_output", call_id = callId, output
    });

    // An interrupted call might already have caused a side effect. Never silently execute it again.
    public static void ClosePendingToolCalls(List<JsonElement> history)
    {
        var completed = history.Where(item => Type(item) == "function_call_output")
            .Select(item => item.GetProperty("call_id").GetString()).ToHashSet(StringComparer.Ordinal);
        var pending = history.Where(item => Type(item) == "function_call")
            .Select(item => item.GetProperty("call_id").GetString()!)
            .Where(callId => !completed.Contains(callId)).Distinct(StringComparer.Ordinal).ToArray();
        foreach (var callId in pending)
        {
            history.Add(ToolOutput(callId, JsonSerializer.Serialize(new
            {
                error = "execution_interrupted",
                message = "No result was recorded. The operation may or may not have executed. Verify its outcome before retrying any side effect."
            })));
        }
    }

    private static string? Type(JsonElement item) => item.TryGetProperty("type", out var type)
        ? type.GetString() : null;
}
