using System.Text.Json;
using MemoryMunchers.Persistence;
using Microsoft.EntityFrameworkCore;

namespace MemoryMunchers.Agents.Tools;

public sealed class GetSavedForecastsTool(MemoryMunchersDbContext db) : IAgentTool
{
    public AgentToolDefinition Definition { get; } = new("get_saved_forecasts",
        "Read forecasts saved in the application database, newest date first. These are saved records, not live weather.",
        JsonSerializer.SerializeToElement(new
        {
            type = "object",
            properties = new { limit = new { type = "integer", description = "Maximum records to return (1 to 100).", minimum = 1, maximum = 100 } },
            required = new[] { "limit" }, additionalProperties = false
        }));

    public async Task<string> ExecuteAsync(JsonElement arguments, AgentToolContext context, CancellationToken cancellationToken)
    {
        if (!arguments.TryGetProperty("limit", out var value) || value.ValueKind != JsonValueKind.Number ||
            !value.TryGetInt32(out var limit) || limit is < 1 or > 100 ||
            arguments.EnumerateObject().Any(property => property.Name != "limit"))
            throw new AgentToolInputException("Provide only a 'limit' integer between 1 and 100.");

        var forecasts = await db.WeatherForecasts.AsNoTracking()
            .OrderByDescending(forecast => forecast.Date).ThenByDescending(forecast => forecast.Id)
            .Take(limit).Select(forecast => new { forecast.Id, forecast.Date, forecast.TemperatureC, forecast.Summary })
            .ToListAsync(cancellationToken);
        return JsonSerializer.Serialize(forecasts, JsonSerializerOptions.Web);
    }
}
