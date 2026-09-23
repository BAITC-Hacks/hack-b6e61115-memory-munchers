using System.Text.RegularExpressions;

namespace MemoryMunchers.Agents;

public sealed class AgentToolRegistry : IAgentToolRegistry
{
    private readonly Dictionary<string, IAgentTool> tools = new(StringComparer.Ordinal);

    public AgentToolRegistry(IEnumerable<IAgentTool> registeredTools)
    {
        foreach (var tool in registeredTools)
        {
            if (!Regex.IsMatch(tool.Definition.Name, "^[a-zA-Z0-9_-]{1,64}$") ||
                !tools.TryAdd(tool.Definition.Name, tool))
            {
                throw new InvalidOperationException($"Invalid or duplicate tool name '{tool.Definition.Name}'.");
            }
        }
    }

    public IReadOnlyList<IAgentTool> Resolve(IEnumerable<string> names) => names.Select(name =>
        tools.TryGetValue(name, out var tool)
            ? tool
            : throw new AgentException("tool_unavailable", $"Configured tool '{name}' is not registered.", 503))
        .ToArray();
}
