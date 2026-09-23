using MemoryMunchers.Agents.OpenAI;
using MemoryMunchers.Agents.Persistence;
using MemoryMunchers.Agents.Tools;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace MemoryMunchers.Agents;

public static class AgentServiceCollectionExtensions
{
    public static IServiceCollection AddAgenticWorkflow(this IServiceCollection services, IConfiguration configuration)
    {
        services.AddOptions<OpenAiOptions>().Bind(configuration.GetSection(OpenAiOptions.SectionName))
            .Validate(options => !string.IsNullOrWhiteSpace(options.Model), "OpenAI:Model is required.")
            .Validate(options => options.MaxOutputTokens is >= 128 and <= 100_000, "OpenAI:MaxOutputTokens must be between 128 and 100000.")
            .Validate(options => options.RequestTimeoutSeconds is >= 1 and <= 600, "OpenAI:RequestTimeoutSeconds must be between 1 and 600.")
            .ValidateOnStart();
        services.AddOptions<AgentOptions>().Bind(configuration.GetSection(AgentOptions.SectionName))
            .Validate(options => options.RunTimeoutSeconds is >= 1 and <= 1800, "Agents:RunTimeoutSeconds must be between 1 and 1800.")
            .Validate(options => options.Definitions is { Length: > 0 }, "Configure at least one agent in Agents:Definitions.")
            .ValidateOnStart();

        services.TryAddSingleton(TimeProvider.System);
        services.AddSingleton<IAgentCatalog, AgentCatalog>();
        services.AddScoped<IAgentToolRegistry, AgentToolRegistry>();
        services.AddScoped<IAgentSessionStore, PostgresAgentSessionStore>();
        services.AddScoped<IAgentSessionLock, PostgresAgentSessionLock>();
        services.AddScoped<IAgentSessionService, AgentSessionService>();
        services.AddScoped<IAgentRunner, AgentRunner>();
        services.AddScoped<IAgentTool, GetCurrentTimeTool>();
        services.AddScoped<IAgentTool, GetSavedForecastsTool>();
        services.AddHttpClient<IAgentModelClient, OpenAiResponsesClient>((provider, client) =>
        {
            client.Timeout = TimeSpan.FromSeconds(provider.GetRequiredService<IOptions<OpenAiOptions>>()
                .Value.RequestTimeoutSeconds);
        });
        return services;
    }
}
