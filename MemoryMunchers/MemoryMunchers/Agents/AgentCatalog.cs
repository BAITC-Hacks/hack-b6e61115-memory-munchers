using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents;

public sealed class AgentCatalog : IAgentCatalog
{
    private readonly Dictionary<string, AgentDefinition> agents;

    public AgentCatalog(IOptions<AgentOptions> options)
    {
        agents = new Dictionary<string, AgentDefinition>(StringComparer.Ordinal);
        foreach (var agent in options.Value.Definitions)
        {
            if (string.IsNullOrWhiteSpace(agent.Id) || agent.Id.Length > 100 ||
                string.IsNullOrWhiteSpace(agent.Name) || string.IsNullOrWhiteSpace(agent.Instructions) ||
                agent.Instructions.Length > 32_000 || agent.MaxModelCalls is < 1 or > 32 ||
                agent.ToolNames is null || agent.ToolNames.Any(string.IsNullOrWhiteSpace) ||
                agent.ToolNames.Distinct(StringComparer.Ordinal).Count() != agent.ToolNames.Length ||
                !agents.TryAdd(agent.Id, agent))
            {
                throw new InvalidOperationException($"Invalid or duplicate agent definition '{agent.Id}'.");
            }
        }
    }

    public IReadOnlyCollection<AgentDefinition> GetAll() => agents.Values.ToArray();

    public AgentDefinition Get(string agentId) => agents.TryGetValue(agentId, out var agent)
        ? agent
        : throw new AgentException("agent_not_found", $"Agent '{agentId}' was not found.", 404);
}
