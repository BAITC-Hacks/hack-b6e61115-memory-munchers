namespace MemoryMunchers.Agents;

public sealed record AgentDefinition
{
    public string Id { get; init; } = "";
    public string Name { get; init; } = "";
    public string Instructions { get; init; } = "";
    public string? Model { get; init; }
    public string[] ToolNames { get; init; } = [];
    public int MaxModelCalls { get; init; } = 32;
}

public sealed class AgentOptions
{
    public const string SectionName = "Agents";
    public int RunTimeoutSeconds { get; set; } = 1800;
    public AgentDefinition[] Definitions { get; set; } = [];
}
