using System.Text.Json;

namespace MemoryMunchers.Agents.Tools;

public sealed class GetCurrentTimeTool(TimeProvider clock) : IAgentTool
{
    public AgentToolDefinition Definition { get; } = new("get_current_time", "Get the current date and time in UTC.",
        JsonSerializer.SerializeToElement(new
        {
            type = "object", properties = new { }, required = Array.Empty<string>(), additionalProperties = false
        }));

    public Task<string> ExecuteAsync(JsonElement arguments, AgentToolContext context, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (arguments.EnumerateObject().Any())
            throw new AgentToolInputException("get_current_time takes no arguments.");
        return Task.FromResult(JsonSerializer.Serialize(new { utc = clock.GetUtcNow() }));
    }
}
