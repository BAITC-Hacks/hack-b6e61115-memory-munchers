using System.Text.Json;
using MemoryMunchers.Agents.Persistence;

namespace MemoryMunchers.Agents;

public interface IAgentCatalog
{
    IReadOnlyCollection<AgentDefinition> GetAll();
    AgentDefinition Get(string agentId);
}

public interface IAgentRunner
{
    Task<AgentRunResult> RunAsync(Guid sessionId, string message, CancellationToken cancellationToken = default);
}

public interface IAgentSessionService
{
    Task<AgentSessionDetails> CreateAsync(string agentId, string? title = null,
        string? additionalInstructions = null, CancellationToken cancellationToken = default);
    Task<AgentSessionDetails> GetAsync(Guid sessionId, CancellationToken cancellationToken = default);
    Task<IReadOnlyList<AgentSessionSummary>> ListAsync(int skip = 0, int take = 50,
        CancellationToken cancellationToken = default);
}

/// <summary>Provider output items must be preserved intact, including opaque reasoning state.</summary>
public interface IAgentModelClient
{
    Task<AgentModelResponse> RespondAsync(AgentModelRequest request, CancellationToken cancellationToken);
}

public interface IAgentTool
{
    AgentToolDefinition Definition { get; }
    Task<string> ExecuteAsync(JsonElement arguments, AgentToolContext context, CancellationToken cancellationToken);
}

public interface IAgentToolRegistry
{
    IReadOnlyList<IAgentTool> Resolve(IEnumerable<string> names);
}

/// <summary>Sessions returned by this scoped store are tracked until SaveAsync.</summary>
public interface IAgentSessionStore
{
    Task AddAsync(AgentSession session, CancellationToken cancellationToken);
    Task<AgentSession?> FindAsync(Guid sessionId, CancellationToken cancellationToken);
    Task<IReadOnlyList<AgentSessionSummary>> ListAsync(int skip, int take, CancellationToken cancellationToken);
    Task SaveAsync(CancellationToken cancellationToken);
}

public interface IAgentSessionLock
{
    /// <summary>Returns null when another process is running this session. Dispose to release.</summary>
    Task<IAsyncDisposable?> TryAcquireAsync(Guid sessionId, CancellationToken cancellationToken);
}

public sealed record AgentToolDefinition(string Name, string Description, JsonElement Parameters);
public sealed record AgentToolContext(Guid SessionId, Guid RunId);
public sealed record AgentToolCall(string CallId, string Name, string Arguments);
public sealed record AgentModelRequest(string Model, string Instructions,
    IReadOnlyList<JsonElement> Input, IReadOnlyList<AgentToolDefinition> Tools);
public sealed record AgentModelResponse(IReadOnlyList<JsonElement> OutputItems, string Text,
    IReadOnlyList<AgentToolCall> ToolCalls);
public sealed record AgentRunResult(Guid SessionId, Guid RunId, string Status, string? Output,
    int ModelCalls, int ToolCalls, string? ErrorCode, string? ErrorMessage,
    DateTimeOffset StartedAt, DateTimeOffset? CompletedAt);
public sealed record AgentSessionSummary(Guid Id, string AgentId, string? Title,
    DateTimeOffset CreatedAt, DateTimeOffset UpdatedAt);
public sealed record AgentSessionDetails(Guid Id, string AgentId, string? Title, string Instructions,
    string Model, IReadOnlyList<string> ToolNames, DateTimeOffset CreatedAt, DateTimeOffset UpdatedAt,
    IReadOnlyList<JsonElement> History, IReadOnlyList<AgentRunResult> Runs);
