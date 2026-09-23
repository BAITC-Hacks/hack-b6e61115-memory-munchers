namespace MemoryMunchers.Agents.Persistence;

public enum AgentRunStatus { Running, Completed, Failed, Cancelled }

public sealed class AgentRun
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid SessionId { get; set; }
    public AgentRunStatus Status { get; set; } = AgentRunStatus.Running;
    public string Input { get; set; } = "";
    public string? Output { get; set; }
    public string? ErrorCode { get; set; }
    public string? ErrorMessage { get; set; }
    public int ModelCalls { get; set; }
    public int ToolCalls { get; set; }
    public DateTimeOffset StartedAt { get; set; }
    public DateTimeOffset? CompletedAt { get; set; }

    public AgentRunResult ToResult() => new(SessionId, Id, Status.ToString(), Output,
        ModelCalls, ToolCalls, ErrorCode, ErrorMessage, StartedAt, CompletedAt);
}
