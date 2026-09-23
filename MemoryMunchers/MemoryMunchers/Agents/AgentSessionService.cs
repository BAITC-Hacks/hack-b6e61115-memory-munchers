using System.Text.Json;
using MemoryMunchers.Agents.OpenAI;
using MemoryMunchers.Agents.Persistence;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents;

public sealed class AgentSessionService(IAgentCatalog catalog, IAgentToolRegistry tools,
    IAgentSessionStore store, IOptions<OpenAiOptions> openAiOptions, TimeProvider clock) : IAgentSessionService
{
    public async Task<AgentSessionDetails> CreateAsync(string agentId, string? title = null,
        string? additionalInstructions = null, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(agentId) || title?.Length > 200 || additionalInstructions?.Length > 8_000)
            throw new AgentException("invalid_session", "Provide an agent ID, a title up to 200 characters, and additional instructions up to 8000 characters.", 400);

        var definition = catalog.Get(agentId);
        tools.Resolve(definition.ToolNames);
        var session = new AgentSession
        {
            AgentId = definition.Id,
            Title = title?.Trim(),
            Instructions = string.IsNullOrWhiteSpace(additionalInstructions) ? definition.Instructions
                : $"{definition.Instructions}\n\nAdditional session instructions:\n{additionalInstructions.Trim()}",
            Model = string.IsNullOrWhiteSpace(definition.Model) ? openAiOptions.Value.Model : definition.Model,
            ToolNamesJson = JsonSerializer.Serialize(definition.ToolNames),
            MaxModelCalls = definition.MaxModelCalls,
            CreatedAt = clock.GetUtcNow(),
            UpdatedAt = clock.GetUtcNow()
        };
        await store.AddAsync(session, cancellationToken);
        return ToDetails(session);
    }

    public async Task<AgentSessionDetails> GetAsync(Guid sessionId, CancellationToken cancellationToken = default)
    {
        var session = await store.FindAsync(sessionId, cancellationToken)
            ?? throw new AgentException("session_not_found", "The agent session was not found.", 404);
        return ToDetails(session);
    }

    public Task<IReadOnlyList<AgentSessionSummary>> ListAsync(int skip = 0, int take = 50,
        CancellationToken cancellationToken = default)
    {
        if (skip < 0 || take is < 1 or > 100)
            throw new AgentException("invalid_pagination", "Skip must be nonnegative and take must be between 1 and 100.", 400);
        return store.ListAsync(skip, take, cancellationToken);
    }

    private static AgentSessionDetails ToDetails(AgentSession session) => new(session.Id, session.AgentId,
        session.Title, session.Instructions, session.Model,
        JsonSerializer.Deserialize<string[]>(session.ToolNamesJson)!, session.CreatedAt, session.UpdatedAt,
        JsonSerializer.Deserialize<JsonElement[]>(session.HistoryJson)!,
        session.Runs.OrderBy(run => run.StartedAt).ThenBy(run => run.Id).Select(run => run.ToResult()).ToArray());
}
