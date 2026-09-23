namespace MemoryMunchers.Agents.Persistence;

public sealed class AgentSession
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid? ShopperId { get; set; }
    public string AgentId { get; set; } = "";
    public string? Title { get; set; }
    public string Instructions { get; set; } = "";
    public string Model { get; set; } = "";
    public string ToolNamesJson { get; set; } = "[]";
    public int MaxModelCalls { get; set; }
    public string HistoryJson { get; set; } = "[]";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
    public List<AgentRun> Runs { get; set; } = [];
}
